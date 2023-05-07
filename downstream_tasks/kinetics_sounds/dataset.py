"""
Custom class for loading audio-visual data 
Modified from https://github.com/s3prl/s3prl/blob/main/s3prl/downstream/example/dataset.py
"""
import random

import os
import csv
import torch
import torch.nn as nn
from torch.utils.data.dataset import Dataset

import torchaudio
import torchvision
from torchaudio.transforms import Resample

# Example parameters
AUDIO_SAMPLE_RATE = 16000
VIDEO_FRAME_RATE = 25
# MIN_SEC = 5
# MAX_SEC = 20
# HEIGHT = 224
# WIDTH = 224


class RandomDataset(Dataset):
    def __init__(self, preprocess_audio, preprocess_video, mode, kinetics_root, **kwargs):
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
        """
        self.kinetics_root = kinetics_root
        self.mode = mode

        if mode == "train":
            self.path = kwargs["train_meta_location"]
        elif mode == "validation":
            self.path = kwargs["val_meta_location"]
        elif mode == "test":
            self.path = kwargs["test_meta_location"]
        print("dataset meta path", self.path)

        file = open(self.path, "r")
        data = list(csv.reader(file, delimiter=","))
        file.close()
        print("data example", data[0])

        self.dataset = data
        self.class_num = 32
        
        self.audio_sample_rates = [AUDIO_SAMPLE_RATE] * len(self)
        self.video_frame_rates = [VIDEO_FRAME_RATE] * len(self)
        self.preprocess_audio = preprocess_audio
        self.preprocess_video = preprocess_video


    # def get_rates(self, idx):
    #     """
    #     Return the audio sample rate and video frame rate of the idx-th video.
    #     (Datasets may contain data with different sample rates)
    #     """
    #     return self.audio_sample_rates[idx], self.video_frame_rates[idx]

    def __getitem__(self, idx):
        # audio_samples = random.randint(
        #     MIN_SEC * AUDIO_SAMPLE_RATE, MAX_SEC * AUDIO_SAMPLE_RATE
        # )
        # video_samples = random.randint(
        #     MIN_SEC * VIDEO_FRAME_RATE, MAX_SEC * VIDEO_FRAME_RATE
        # )
        path = os.path.join(self.kinetics_root, self.dataset[idx][0])

        # You may use the following function to read video data:
        frames, wav, meta = torchvision.io.read_video(path, pts_unit="sec", output_format="TCHW")
        label = int(self.dataset[idx][1])

        wav = wav.mean(dim=0).squeeze(0)
        while wav.shape[0] == 0:
            print(path)
            rand_idx = random.randint(0, len(self.dataset)-1)
            path = os.path.join(self.kinetics_root, self.dataset[rand_idx][0])
            frames, wav, meta = torchvision.io.read_video(path, pts_unit="sec", output_format="TCHW")
            label = int(self.dataset[rand_idx][1])
            wav = wav.mean(dim=0).squeeze(0)

        audio_sr, video_fps = meta['audio_fps'], meta['video_fps']
        # self.get_rates(idx)
        
        # wav = torch.randn(audio_samples)
        if self.preprocess_audio is not None:
            processed_wav = self.preprocess_audio(wav, audio_sr)
        else:
            processed_wav = wav

        # frames = torch.randn(video_samples, 3, HEIGHT, WIDTH)
        frames = frames.float()
        if self.preprocess_video is not None:
            processed_frames = self.preprocess_video(frames, video_fps)
        else:
            processed_frames = frames

        return processed_wav, processed_frames, label

    def __len__(self):
        return len(self.dataset)

    def collate_fn(self, samples):
        wavs, videos, labels = [], [], []
        for wav, frames, label in samples:
            wavs.append(wav)
            videos.append(frames)
            labels.append(label)
        return wavs, videos, labels
