
import random
import string
from typing import cast

from textual.app import App
from textual.widgets import ListView

from simple_downloader.App import Download, DownloadApp, DownloadItem
from simple_downloader.executor import ExecutableName
from simple_downloader.models import DownloadJob, DownloadRequest
from simple_downloader.sources import VideoMetadata


class SimpleDownlader:
    def __init__(self, app: App[None]):
        self.app = cast(DownloadApp, app)

    async def add_item(self):
        assert self.app.download_manager is not None
        assert self.app.source is not None

        list_view_downloads_item = self.app.query_one("#download-list", ListView)
        download_request = DownloadRequest(
            url=""
        )
        job: DownloadJob = await self.app.download_manager.enqueue(request=download_request)
        metadata: VideoMetadata = await self.app.source.get_source(executable_name=ExecutableName.YT_DLP).metadata(url="")

        caracteres = string.ascii_uppercase + string.digits
        pnr = ''.join(random.choices(caracteres, k=6))
        item = DownloadItem(
            download=Download(
                download_id=pnr,
                filename=metadata.title,
                total_bytes=metadata
            )
        )

    def start_download(self):
        ...
