
import os
import pathlib
from functools import cached_property
from typing import Optional

import aiohttp
from frozendict import frozendict

from aio.api.github import exceptions as github_exceptions
from aio.run import runner

from envoy.base import utils
from envoy.base.utils import exceptions, interface, typing


ENV_GITHUB_TOKEN = 'GITHUB_TOKEN'
NOTIFY_MSGS: frozendict = frozendict(
    release=(
        "Release created ({change[release][version]}): "
        "{change[release][date]}"),
    dev="Repo set to dev ({change[dev][version]})",
    sync="Repo synced",
    publish="Repo published")
COMMIT_MSGS: frozendict = frozendict(
    release="repo: Release `{change[release][version]}`",
    dev="repo: Dev `{change[dev][version]}`",
    sync="repo: Sync")


class BaseProjectRunner(runner.Runner):

    @property
    def github_token(self) -> Optional[str]:
        """Github access token."""
        if self.args.github_token:
            return pathlib.Path(self.args.github_token).read_text().strip()
        return os.getenv(ENV_GITHUB_TOKEN)

    @cached_property
    def path(self) -> pathlib.Path:
        return pathlib.Path(self.args.path)

    @cached_property
    def project(self) -> interface.IProject:
        return utils.Project(
            path=self.path,
            session=self.session,
            repo=self.repo_name,
            github_token=self.github_token)

    @property
    def repo_name(self) -> Optional[str]:
        return self.args.repo

    @cached_property
    def session(self) -> aiohttp.ClientSession:
        """HTTP client session."""
        return aiohttp.ClientSession()


class ProjectRunner(BaseProjectRunner):

    @property
    def command(self) -> bool:
        return self.args.command

    @property
    def nocommit(self) -> bool:
        return (
            self.args.command == "publish"
            or self.args.nocommit)

    @property
    def nosync(self) -> bool:
        return self.args.nosync

    @property
    def patch(self) -> bool:
        return self.args.patch

    def add_arguments(self, parser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "command",
            choices=["sync", "release", "dev", "publish"])
        parser.add_argument("path", default=".")
        parser.add_argument("--github_token")
        parser.add_argument("--nosync", action="store_true")
        parser.add_argument("--nocommit", action="store_true")
        parser.add_argument("--patch", action="store_true")
        parser.add_argument("--repo", default="")

        parser.add_argument("--publish-assets", default="")
        parser.add_argument("--publish-commitish", default="")
        parser.add_argument("--publish-dev", action="store_true")
        parser.add_argument("--publish-latest", action="store_true")
        parser.add_argument("--publish-generate-notes", action="store_true")

    async def commit(
            self,
            change: typing.ProjectChangeDict) -> None:
        msg = self.msg_for_commit(change)
        async for path in self.project.commit(change, msg):
            self.log.info(f"[git] add: {path}")
        self.log.info(f"[git] commit: \"{msg}\"")

    async def handle_action(self) -> typing.ProjectChangeDict:
        change: typing.ProjectChangeDict = {}
        if self.command == "publish":
            return dict(
                publish=await self.run_publish(
                    assets=self.args.publish_assets,
                    commitish=self.args.publish_commitish,
                    dev=self.args.publish_dev,
                    latest=self.args.publish_latest))
        if self.command == "dev":
            change["dev"] = await self.run_dev()
        if self.command == "release":
            change["release"] = await self.run_release()
        if not self.nosync:
            change["sync"] = await self.run_sync()
        return change

    def msg_for_commit(self, change: typing.ProjectChangeDict) -> str:
        return COMMIT_MSGS[self.command].format(change=change)

    def notify_complete(self, change: typing.ProjectChangeDict) -> None:
        self.log.notice(NOTIFY_MSGS[self.command].format(change=change))

    @runner.cleansup
    @runner.catches(
        (exceptions.DevError,
         exceptions.ReleaseError,
         github_exceptions.TagError,
         github_exceptions.TagExistsError))
    async def run(self) -> None:
        change = await self.handle_action()
        if not self.nocommit:
            await self.commit(change)
        self.notify_complete(change)

    async def run_dev(self) -> typing.ProjectDevResultDict:
        change = await self.project.dev(patch=self.patch)
        self.log.success(f"[version] {change['version']}")
        self.log.success(
            f"[changelog] add: {change['old_version']}")
        return change

    async def run_publish(self, **kwargs) -> typing.ProjectPublishResultDict:
        change = await self.project.publish(**kwargs)
        self.log.success(
            f"[release] Release ({change['tag_name']}) "
            f"created from branch/commit: {change['commitish']}\n"
            f"    {change['url']}")
        return change

    async def run_release(self) -> typing.ProjectReleaseResultDict:
        change = await self.project.release()
        self.log.success(f"[version] {change['version']}")
        self.log.success(f"[changelog] current: {change['date']}")
        return change

    async def run_sync(self) -> typing.ProjectSyncResultDict:
        change = await self.project.sync()
        self._log_changelog(change["changelog"])
        self._log_inventory(change["inventory"])
        return change

    def _log_changelog(self, results: typing.SyncResultDict) -> None:
        if not results:
            self.log.success("[changelog] up to date")
        for version, result in results.items():
            self.log.success(f"[changelog] add: {version}")

    def _log_inventory(self, results: typing.SyncResultDict) -> None:
        if not results:
            self.log.success("[inventory] up to date")
        for version, result in results.items():
            if result:
                self.log.success(
                    "[inventory] update: "
                    f"{utils.minor_version_for(version)} -> {version}")
            else:
                self.log.warning(
                    "[inventory] newer version available "
                    f"({version}), but no inventory found")


class ProjectDataRunner(BaseProjectRunner):

    def add_arguments(self, parser) -> None:
        super().add_arguments(parser)
        parser.add_argument("path", default=".")
        parser.add_argument("--repo", default="")
        parser.add_argument("--github_token")

    @runner.cleansup
    async def run(self) -> None:
        print(await self.project.json_data)
