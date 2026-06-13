# -*- coding: utf-8 -*-
import warnings

# 忽略 Pydantic 关于以 "model_" 开头的字段与保护命名空间冲突的 UserWarning 警告（例如 model_name, model_id, model_type）
warnings.filterwarnings("ignore", message='.*has conflict with protected namespace "model_".*')

from app.server import app

__all__ = ["app"]
