# Copyright (c) Facebook, Inc. All Rights Reserved

# -*- coding: utf-8 -*- #
"""*********************************************************************************************"""
#   FileName     [ upstream/hubert/expert.py ]
#   Synopsis     [ the HuBERT wrapper ]
#   Author       [ Kushal Lakhotia ]
"""*********************************************************************************************"""

import logging
from pathlib import Path

import fairseq
import torch
import torch.nn.functional as F
import torchaudio
from torch.nn.utils.rnn import pad_sequence

from interfaces import UpstreamBase

SAMPLE_RATE = 16000
EXAMPLE_SEC = 5

logger = logging.getLogger(__name__)


# class UpstreamExpert(UpstreamBase):
#     def __init__(self, ckpt, **kwargs):
#         super().__init__(**kwargs)
#         model, task_cfg = load_converted_model(ckpt)
#         self.model = model
#         self.task_cfg = task_cfg

#         self.model.feature_grad_mult = 0.0
#         self.model.encoder.layerdrop = 0.0

#         if len(self.hooks) == 0:
#             module_name = "self.model.encoder.layers"
#             for module_id in range(len(eval(module_name))):
#                 self.add_hook(
#                     f"{module_name}[{module_id}]",
#                     lambda input, output: input[0].transpose(0, 1),
#                 )
#             self.add_hook("self.model.encoder", lambda input, output: output[0])

#             def postprocess(xs):
#                 names, hiddens = zip(*xs)
#                 unpad_len = min([hidden.size(1) for hidden in hiddens])
#                 hiddens = [hidden[:, :unpad_len, :] for hidden in hiddens]
#                 return list(zip(names, hiddens))

#             self.hook_postprocess = postprocess

#     def get_downsample_rates(self, key: str) -> int:
#         return 320

#     def forward(self, wavs):
#         if self.task_cfg.normalize:
#             wavs = [F.layer_norm(wav, wav.shape) for wav in wavs]

#         device = wavs[0].device
#         wav_lengths = torch.LongTensor([len(wav) for wav in wavs]).to(device)
#         wav_padding_mask = ~torch.lt(
#             torch.arange(max(wav_lengths)).unsqueeze(0).to(device),
#             wav_lengths.unsqueeze(1),
#         )
#         padded_wav = pad_sequence(wavs, batch_first=True)

#         features, feat_padding_mask = self.model.extract_features(
#             padded_wav,
#             padding_mask=wav_padding_mask,
#             mask=None,
#         )

#         # This forward function only does the model forward
#         # The return dict is then handled by UpstreamBase's hooks


class UpstreamExpert(UpstreamBase):
    def __init__(self, ckpt, **kwargs):
        super().__init__(**kwargs)

        model, cfg, task = fairseq.checkpoint_utils.load_model_ensemble_and_task([ckpt])
        self.model = model[0]
        self.task = task

        self.model.feature_grad_mult = 0.0
        self.model.encoder.layerdrop = 0.0
        self.audio_sample_rate = 16000

        if len(self.hooks) == 0:
            module_name = "self.model.encoder.layers"
            for module_id in range(len(eval(module_name))):
                self.add_hook(
                    f"{module_name}[{module_id}]",
                    lambda input, output: input[0].transpose(0, 1),
                )
            self.add_hook("self.model.encoder", lambda input, output: output[0])

            def postprocess(xs):
                names, hiddens = zip(*xs)
                names = [name + "_audio" for name in names]
                unpad_len = min([hidden.size(1) for hidden in hiddens])
                hiddens = [hidden[:, :unpad_len, :] for hidden in hiddens]
                return list(zip(names, hiddens))

            self.hook_postprocess = postprocess

    def get_downsample_rates(self, key: str) -> int:
        return 320

    def preprocess_video(self, video, video_frame_rate):
        return video[0][0][0]

    def preprocess_audio(self, audio, audio_sample_rate):
        """
        Replace this function to preprocess audio waveforms into your input format
        audio: (audio_channels, audio_length), where audio_channels is usually 1 or 2
        """
        # Resample audio
        if audio_sample_rate != self.audio_sample_rate:
            audio = torchaudio.functional.resample(
                audio, audio_sample_rate, self.audio_sample_rate
            )

        return audio

    def forward(self, source):
        wavs, video = zip(*source)

        if self.task.cfg.normalize:
            wavs = [F.layer_norm(wav, wav.shape) for wav in wavs]

        device = wavs[0].device
        wav_lengths = torch.LongTensor([len(wav) for wav in wavs]).to(device)
        wav_padding_mask = ~torch.lt(
            torch.arange(max(wav_lengths)).unsqueeze(0).to(device),
            wav_lengths.unsqueeze(1),
        )
        padded_wav = pad_sequence(wavs, batch_first=True)

        features, feat_padding_mask = self.model.extract_features(
            padded_wav,
            padding_mask=wav_padding_mask,
            mask=None,
        )

        # This forward function only does the model forward
        # The return dict is then handled by UpstreamBase's hooks
