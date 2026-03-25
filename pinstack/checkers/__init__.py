"""Checker registry -- imports all checker modules."""
from typing import List, Type

from pinstack.core import Checker
from pinstack.checkers.requirements import RequirementsChecker
from pinstack.checkers.pyproject import PyprojectChecker

ALL_CHECKERS = [RequirementsChecker, PyprojectChecker]  # type: List[Type[Checker]]
