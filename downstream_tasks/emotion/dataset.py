import json
from os.path import join as path_join
import os
import torchaudio
import torchvision.io
from torch.utils.data import Dataset
import torch
class IEMOCAPDataset(Dataset):
    def __init__(self, iemocap_root, meta_path, preprocess=None, preprocess_audio=None, preprocess_video=None, **kwargs):
        
        self.iemocap_root = iemocap_root        
        with open(meta_path, 'r') as f:
            self.dataset = json.load(f)
 
        self.class_dict = self.dataset['labels']
        self.idx2emotion = {value: key for key, value in self.class_dict.items()}
        self.meta_data = self.dataset['meta_data']
        
        self.preprocess = preprocess
        self.preprocess_audio = preprocess_audio
        self.preprocess_video = preprocess_video
        self.upstream_name = kwargs['upstream']
        self.upstream_feature_selection = kwargs['upstream_feature_selection']
        self.pooled_features_path = kwargs['pooled_features_path']

    def __getitem__(self, idx):
        label = self.meta_data[idx]['label']
        label = self.class_dict[label]
        
        fname = self.meta_data[idx]['path']
        basename = os.path.basename(self.meta_data[idx]['path'])
        
        if self.pooled_features_path:
            pooled_feature_path = f"{self.pooled_features_path}/{self.upstream_name}_{self.upstream_feature_selection}/{basename}_pooled.pt"
            if os.path.exists(pooled_feature_path):
                pooled_feature = torch.load(pooled_feature_path)
                return pooled_feature, pooled_feature, label, True

        feature_path = f"{self.iemocap_root}/preprocess_features/{self.upstream_name}/{fname.split('/')[0]}/{fname.split('/')[-2]}/{basename}.pt"
        
        if os.path.exists(feature_path):
            processed_wav, processed_frames = torch.load(feature_path)
        else:
            wav, audio_sr = torchaudio.load(path_join(self.iemocap_root, self.meta_data[idx]['path']))
            avi_path = path_join(self.iemocap_root, "clips", os.path.splitext(self.meta_data[idx]['path'])[0].replace('sentences/wav/', '')+'.mp4')
            frames, _, rates = torchvision.io.read_video(avi_path, pts_unit="sec", output_format="TCHW")
            video_fps = rates["video_fps"]
            
            if self.preprocess is not None:
                processed_frames, processed_wav = self.preprocess(frames, wav, video_fps, audio_sr)
            else:    
                if self.preprocess_audio is not None:
                    processed_wav = self.preprocess_audio(wav, audio_sr)
                else:
                    processed_wav = wav

                if self.preprocess_video is not None:
                    processed_frames = self.preprocess_video(frames, video_fps)
                else:
                    processed_frames = frames

        return processed_wav, processed_frames, label, basename
        
    def __len__(self):
        return len(self.meta_data)

def collate_fn(samples):
    wavs, videos, *others = zip(*samples)
    
    return wavs, videos, *others
