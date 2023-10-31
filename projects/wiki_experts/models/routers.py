import math
import torch
import torch.nn as nn
from enum import Enum

from mttl.models.adapters import Adapter, MergableAdapter
from typing import Any, Dict
import torch.nn.functional as F
from mttl.models.modifiers.experts import add_expert_to_transformer
from mttl.models.adapters import SkilledLoRA, LoRA, SkilledLoRA_MergeLoraAfterOP
from abc import abstractmethod, ABCMeta, abstractproperty
from mttl.global_vars import EPS

MULTI_EXPERT_ROUTERS = {}


def register_multi_expert_selector(name):
    print("Registering multi-expert selector..." + name)

    def _thunk(fn):
        if name in MULTI_EXPERT_ROUTERS:
            raise ValueError(f"Cannot register duplicate model modifier ({name})")
        MULTI_EXPERT_ROUTERS[name] = fn
        return fn

    return _thunk


class Router:
    @abstractmethod
    def forward(self, input, **kwargs):
        pass

    @abstractmethod
    def get_routing_weights(self):
        pass

    @abstractproperty
    def name(self):
        pass


@register_multi_expert_selector("poly_router")
class Multi_ExpertRouter(torch.nn.Module, Router):
    """
    Implements routing at a per-layer or pe-model level
    """

    def __init__(self, config, expert_names=[]):
        super().__init__()
        self.config = config
        self.expert_names: list = expert_names

        self.module_logits = nn.Parameter(
            torch.empty(len(expert_names)).uniform_(-1e-3, 1e-3)
        )

        self.__layer_name__ = f"poly_router"

    def resize_module_logits(self, expet_names: list):
        self.expert_names += expet_names
        self.module_logits.data = torch.empty(len(self.expert_names)).uniform_(
            -1e-3, 1e-3
        )

    @property
    def name(self):
        return f"{self.__layer_name__}"

    def forward(self, *args, **kwargs):
        module_logits = torch.sigmoid(self.module_logits)
        module_weights = module_logits / (module_logits.sum(dim=-1, keepdim=True) + EPS)
        return {k: v for k, v in zip(self.expert_names, module_weights)}

    def get_routing_weights(self):
        return self.forward()
