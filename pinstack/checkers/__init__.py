"""Checker registry -- imports all checker modules."""

from pinstack.checkers.requirements import RequirementsChecker
from pinstack.checkers.pyproject import PyprojectChecker
from pinstack.checkers.package_json import PackageJsonChecker
from pinstack.checkers.package_lock import PackageLockChecker
from pinstack.checkers.yarn_lock import YarnLockChecker
from pinstack.checkers.pnpm_lock import PnpmLockChecker
from pinstack.checkers.dockerfile import DockerfileChecker
from pinstack.checkers.compose import ComposeChecker
from pinstack.checkers.github_actions import GitHubActionsChecker
from pinstack.checkers.go import GoChecker
from pinstack.checkers.cargo import CargoChecker
from pinstack.checkers.gemfile import GemfileChecker
from pinstack.checkers.terraform import TerraformChecker
from pinstack.checkers.helm import HelmChecker
from pinstack.checkers.maven import MavenChecker
from pinstack.checkers.gradle import GradleChecker

ALL_CHECKERS = [
    RequirementsChecker,
    PyprojectChecker,
    PackageJsonChecker,
    PackageLockChecker,
    YarnLockChecker,
    PnpmLockChecker,
    DockerfileChecker,
    ComposeChecker,
    GitHubActionsChecker,
    GoChecker,
    CargoChecker,
    GemfileChecker,
    TerraformChecker,
    HelmChecker,
    MavenChecker,
    GradleChecker,
]
