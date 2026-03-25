"""Checker registry -- imports all checker modules."""
from typing import List, Type
from pinstack.core import Checker

# Will be populated as checker modules are added
ALL_CHECKERS = []  # type: List[Type[Checker]]
