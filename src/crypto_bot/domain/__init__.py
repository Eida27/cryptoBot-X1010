"""Shared contracts and deterministic financial types."""

from decimal import DefaultContext, getcontext

DefaultContext.prec = 34
getcontext().prec = 34
