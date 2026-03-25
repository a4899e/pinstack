"""Checker registry -- imports all checker modules."""
from typing import List, Type

from pinstack.core import Checker
from pinstack.checkers.requirements import RequirementsChecker
from pinstack.checkers.pyproject import PyprojectChecker
from pinstack.checkers.package_json import PackageJsonChecker
from pinstack.checkers.package_lock import PackageLockChecker
from pinstack.checkers.yarn_lock import YarnLockChecker
from pinstack.checkers.pnpm_lock import PnpmLockChecker

ALL_CHECKERS = [  # type: List[Type[Checker]]
    RequirementsChecker,
    PyprojectChecker,
    PackageJsonChecker,
    PackageLockChecker,
    YarnLockChecker,
    PnpmLockChecker,
]
