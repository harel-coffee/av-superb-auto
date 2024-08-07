import csv
import math
import os
import random

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import average_precision_score
from torch.distributed import is_initialized
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import (
    DataLoader,
    Dataset,
    DistributedSampler,
    WeightedRandomSampler,
)

from .dataset import AudiosetDataset
from .model import Model


def get_ddp_sampler(dataset: Dataset, epoch: int):
    if is_initialized():
        sampler = DistributedSampler(dataset)
        sampler.set_epoch(epoch)
    else:
        sampler = None
    return sampler


class DownstreamExpert(nn.Module):
    """
    Used to handle downstream-specific operations
    eg. downstream forward, metric computation, contents to log
    """

    def __init__(
        self,
        preprocess,
        preprocess_audio,
        preprocess_video,
        upstream_dim,
        downstream_expert,
        expdir,
        **kwargs,
    ):
        """
        Your dataset should take two preprocessing transform functions,
        preprocess_audio and preprocess_video as input.
        These two functions will be defined by the upstream models, and
        will transform raw waveform & video frames into the desired
        format of the upstream model.
        They take two arguments, the input audio/video Tensor, and the
        audio sample rate/video frame rate, respectively.
        Optionally, if you wish to obtain raw data for testing purposes,
        you may also specify these functions to be None, and return the
        raw data when the functions are not defined.
        Args:
            preprocess_audio: function
                Defined by specified upstream model, transforms raw waveform into
                desired input format.
                Takes two arguments, input audio Tensor, and audio sample rate.
            preprocess_video: function
                Defined by specified upstream model, transforms raw video frames
                into desired input format.
                Takes two arguments, input video Tensor, and video frame rate.
            upstream_dim: int
                Different upstream models will give different representation dimension
                You might want to first project them to the same dimension
            downstream_expert: dict
                The 'downstream_expert' field specified in your downstream config file
                eg. downstream/example/config.yaml
            expdir: string
                The expdir from command-line argument, you should save all results into
                this directory, like some logging files.
            **kwargs: dict
                All the arguments specified by the argparser in run_downstream.py
                and all the other fields in config.yaml, in case you need it.
                Note. Feel free to add new argument for __init__ as long as it is
                a command-line argument or a config field. You can check the constructor
                code in downstream/runner.py
        """

        super(DownstreamExpert, self).__init__()
        self.upstream_dim = upstream_dim
        self.datarc = downstream_expert["datarc"]
        self.modelrc = downstream_expert["modelrc"]

        self.train_dataset = AudiosetDataset(
            csvname="audioset_train.csv",
            audioset_root=self.datarc["train_root"],
            preprocess=preprocess,
            preprocess_audio=preprocess_audio,
            preprocess_video=preprocess_video,
            upstream=kwargs["upstream"],
            pooled_features_path=kwargs["pooled_features_path"],
            upstream_feature_selection=kwargs["upstream_feature_selection"],
            **self.datarc,
        )

        self.dev_dataset = AudiosetDataset(
            csvname="audioset_dev.csv",
            audioset_root=self.datarc["train_root"],
            preprocess=preprocess,
            preprocess_audio=preprocess_audio,
            preprocess_video=preprocess_video,
            upstream=kwargs["upstream"],
            pooled_features_path=kwargs["pooled_features_path"],
            upstream_feature_selection=kwargs["upstream_feature_selection"],
            **self.datarc,
        )
        self.test_dataset = AudiosetDataset(
            csvname="audioset_test.csv",
            audioset_root=self.datarc["test_root"],
            preprocess=preprocess,
            preprocess_audio=preprocess_audio,
            preprocess_video=preprocess_video,
            upstream=kwargs["upstream"],
            pooled_features_path=kwargs["pooled_features_path"],
            upstream_feature_selection=kwargs["upstream_feature_selection"],
            **self.datarc,
        )

        self.connector = nn.Linear(upstream_dim, self.modelrc["input_dim"])

        self.model = Model(
            output_class_num=self.train_dataset.class_num,
            **self.modelrc,
        )
        self.objective = nn.BCEWithLogitsLoss()
        self.register_buffer("best_score", torch.zeros(1))
        # for mAP
        self.predicts = torch.empty((0, 527))
        self.targets = torch.empty((0, 527))

    # Interface
    def get_dataloader(self, split, epoch: int = 0):
        if split == "train":
            return self._get_train_dataloader(self.train_dataset, epoch)
        elif split == "dev":
            return self._get_eval_dataloader(self.dev_dataset)
        elif split == "test":
            return self._get_eval_dataloader(self.test_dataset)

    def _get_train_dataloader(self, dataset, epoch: int):
        sampler = get_ddp_sampler(dataset, epoch)
        return DataLoader(
            dataset,
            batch_size=self.datarc["train_batch_size"],
            shuffle=(sampler is None),
            sampler=sampler,
            num_workers=self.datarc["num_workers"],
            collate_fn=dataset.collate_fn,
        )

    def _get_eval_dataloader(self, dataset):
        return DataLoader(
            dataset,
            batch_size=self.datarc["eval_batch_size"],
            shuffle=False,
            num_workers=self.datarc["num_workers"],
            collate_fn=dataset.collate_fn,
        )

    # Interface
    def forward(self, split, features, labels, basenames, records, **kwargs):
        """
        Args:
            split: string
                'train'
                    when the forward is inside the training loop

                'dev', 'test' or more
                    when the forward is inside the evaluation loop

            features:
                list of unpadded features [feat1, feat2, ...]
                each feat is in torch.FloatTensor and already
                put in the device assigned by command-line args

            your_other_contents1, ... :
                in the order defined by your dataloader (dataset + collate_fn)
                these are all in cpu, and you can move them to the same device
                as features

            records:
                defaultdict(list), by appending contents into records,
                these contents can be averaged and logged on Tensorboard
                later by self.log_records (also customized by you)

                Note1. downstream/runner.py will call self.log_records
                    1. every `log_step` during training
                    2. once after evalute the whole dev/test dataloader

                Note2. `log_step` is defined in your downstream config
                eg. downstream/example/config.yaml

        Return:
            loss:
                the loss to be optimized, should not be detached
                a single scalar in torch.FloatTensor
        """
        features = pad_sequence(features, batch_first=True)
        features = self.connector(features)
        predicted = self.model(features)

        utterance_labels = labels

        labels = torch.LongTensor(utterance_labels).to(
            features.device
        )  # accum_step*class_num
        predicted = predicted.float()
        labels = labels.float()
        loss = self.objective(predicted, labels)
        records["loss"].append(loss.item())
        self.predicts = torch.cat((self.predicts.to("cuda"), predicted.to("cuda")), 0)
        self.targets = torch.cat((self.targets.to("cuda"), labels.to("cuda")), 0)

        return loss

    # interface
    def log_records(
        self, split, records, logger, global_step, batch_ids, total_batch_num, **kwargs
    ):
        """
        Args:
            split: string
                'train':
                    records and batchids contain contents for `log_step` batches
                    `log_step` is defined in your downstream config
                    eg. downstream/example/config.yaml

                'dev', 'test' or more:
                    records and batchids contain contents for the entire evaluation dataset

            records:
                defaultdict(list), contents already prepared by self.forward

            logger:
                Tensorboard SummaryWriter
                please use f'{your_task_name}/{split}-{key}' as key name to log your contents,
                preventing conflict with the logging of other tasks

            global_step:
                The global_step when training, which is helpful for Tensorboard logging

            batch_ids:
                The batches contained in records when enumerating over the dataloader

            total_batch_num:
                The total amount of batches in the dataloader

        Return:
            a list of string
                Each string is a filename we wish to use to save the current model
                according to the evaluation result, like the best.ckpt on the dev set
                You can return nothing or an empty list when no need to save the checkpoint
        """
        mAP = average_precision_score(
            self.targets.cpu().detach().numpy(),
            self.predicts.cpu().detach().numpy(),
            average="macro",
        )
        records["mAP"].append(mAP)
        print()
        save_names = []
        for key, values in records.items():
            average = torch.FloatTensor(values).mean().item()
            logger.add_scalar(
                f"audioset/{split}-{key}", average, global_step=global_step
            )
            print(f"{split}_{key}: {average}")
            if split == "dev" and key == "mAP" and average > self.best_score:
                self.best_score = torch.ones(1) * average
                save_names.append(f"{split}-best.ckpt")
        self.predicts = torch.empty((0, 527))
        self.targets = torch.empty((0, 527))
        return save_names
