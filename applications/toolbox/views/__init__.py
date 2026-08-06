# -*- coding: utf-8 -*-
from fastapi import APIRouter

from .generate_view import generate

toolbox = APIRouter()

toolbox.include_router(generate, prefix="/generate")
