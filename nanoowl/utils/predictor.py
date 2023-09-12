
import PIL.Image
import torch
from transformers import (
    OwlViTProcessor,
    OwlViTForObjectDetection,
    OwlViTVisionModel
)
from transformers.models.owlvit.modeling_owlvit import (
    OwlViTObjectDetectionOutput
)
from torchvision.transforms import (
    ToTensor,
    Normalize,
    Resize,
    Compose
)
from typing import Sequence, List, Tuple
import torch.nn as nn
import time
import numpy as np
from nanoowl.utils.tensorrt import load_image_encoder_engine


def remap_output(output, device):
    if isinstance(output, torch.Tensor):
        return output.to(device)
    else:
        res = output.__class__
        resdict = {}
        for k, v in output.items():
            resdict[k] = remap_output(v,device)
        return res(**resdict)


class Predictor(object):
    def __init__(self, threshold=0.1, device="cuda", vision_engine=None):
        self.processor = OwlViTProcessor.from_pretrained("google/owlvit-base-patch32", device_map=device)
        self.model = OwlViTForObjectDetection.from_pretrained("google/owlvit-base-patch32", device_map=device)
        self.threshold = threshold
        self.device = device
        self.times = {}

        # use transform: huggingface preprocessing is very slow
        self._transform = Compose([
            ToTensor(),
            Resize((768, 768)).to(device),
            Normalize(
                mean=[0.48145466, 0.4578275, 0.40821073],
                std=[0.26862954, 0.26130258, 0.27577711]
            ).to(device)
        ])

        if vision_engine is not None:
            vision_model_trt = load_image_encoder_engine(vision_engine, self.model.owlvit.vision_model.post_layernorm)
            self.model.owlvit.vision_model = vision_model_trt

    def predict(self, image: PIL.Image.Image, texts: Sequence[str]):

        # Preprocess text
        inputs_text = self.processor(text=texts, return_tensors="pt")

        # Preprocess image
        inputs_images = {"pixel_values": self._transform(image)[None, ...]}
        inputs = {}
        inputs.update(inputs_text)
        inputs.update(inputs_images)

        # Ensure all devices on specified device
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        # Run model
        outputs = self.model(**inputs)

        # Copy outputs to CPU (for postprocessing)
        outputs = remap_output(outputs, "cpu")

        # Postprocess output
        target_sizes = torch.Tensor([image.size[::-1]])
        results = self.processor.post_process_object_detection(outputs=outputs, target_sizes=target_sizes, threshold=self.threshold)

        # Format output
        i = 0
        boxes, scores, labels = results[i]["boxes"], results[i]["scores"], results[i]["labels"]
        detections = []
        for box, score, label in zip(boxes, scores, labels):
            detection = {"bbox": box.tolist(), "score": float(score), "label": int(label), "text": texts[label]}
            detections.append(detection)

        return detections