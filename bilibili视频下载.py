from requests import get
from pathlib import Path
from rich import print
from rich.progress import (SpinnerColumn, BarColumn, DownloadColumn, Progress, TextColumn,
                           TimeRemainingColumn, TimeElapsedColumn)
from rich.prompt import Prompt
from textwrap import dedent
from yarl import URL
from asyncio import gather, run, create_task
from aiohttp import ClientSession, ClientTimeout
from ffmpeg import input as ffmpeg_input, output as ffmpeg_output, run as ffmpeg_run

SAVE_DIR = '/home/sika/视频/bilibili'
YELLOW = 'bright_yellow'
GREEN = 'bright_green'
CYAN = 'bright_cyan'
DELIMITER = '='*25
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36'


class DownloadBiLiBiLi:
    timeout = 60 * 5
    save_path = Path(SAVE_DIR)

    def __init__(self, url: str) -> None:
        if not (url := url.split('?')[0]).endswith('/'):
            url += '/'
        self.url = url
        with open(Path(__file__).with_name('cookie.txt')) as f:
            cookie = f.read()
        self.headers = {
            'User-Agent': USER_AGENT,
            'Cookie': cookie,
            'Referer': self.url
        }

    def run(self) -> None:
        try:
            base_info_api = 'https://api.bilibili.com/x/web-interface/view?bvid='
            bvid = self.url.rsplit('/', 2)[1]
            base_info_dict = self._request_json(base_info_api + bvid)

            cid = base_info_dict['data']['cid']
            self.title = base_info_dict['data']['title']
            for i in {'/', '\\', '|', '<', '>', '\'', '\"', '?', ':', '*', '\x00'}:
                self.title = self.title.replace(i, ' ')

            info_api = 'https://api.bilibili.com/x/player/wbi/playurl?'
            info_dict = self._request_json(f'{info_api}bvid={bvid}&cid={cid}&fnval=4048')
            self._extract_urls(info_dict)

            self.save_path.mkdir(parents=True, exist_ok=True)
            run(self._download_merge())
        except Exception as e:
            print(e)

    def _request_json(self, url: str) -> dict:
        response = get(url, headers=self.headers)
        response.encoding = response.apparent_encoding
        return response.json()

    def _extract_urls(self, info_dict: dict) -> None:
        width = int(info_dict['data']['dash']['video'][2]['width'])
        height = int(info_dict['data']['dash']['video'][2]['height'])
        if max(width, height) < 1920:
            if width == int(info_dict['data']['dash']['video'][0]['width']):
                index = 2
            else:
                index = 0
                width = int(info_dict['data']['dash']['video'][index]['width'])
                height = int(info_dict['data']['dash']['video'][index]['height'])
        else:
            index = 2
        if max(width, height) < 1920:
            color = YELLOW
        else:
            color = CYAN

        print(f'[{CYAN}]\n{self.title}')
        print(f'[{color}]清晰度：{width} × {height}')

        self.video_urls = (
            info_dict['data']['dash']['video'][index]['baseUrl'],
            info_dict['data']['dash']['video'][index]['backupUrl'][0]
        )
        self.audio_urls = (
            info_dict['data']['dash']['audio'][0]['baseUrl'],
            info_dict['data']['dash']['audio'][0]['backupUrl'][0],
        )

    async def _download_merge(self) -> None:
        video_path = self.save_path.joinpath(self.title+'_video.mp4')
        audio_path = self.save_path.joinpath(self.title+'_audio.mp3')
        filepath = self.save_path.joinpath(self.title+'.mp4')
        with self._progress_object() as progress:
            tasks = [
                create_task(self._download_video_or_audio(video_path, self.video_urls, progress)),
                create_task(self._download_video_or_audio(audio_path, self.audio_urls, progress))
            ]
            print('')
            await gather(*tasks)
        self._merge(video_path, audio_path, filepath)

    @staticmethod
    def _progress_object() -> Progress:
        return Progress(
            TextColumn('[progress.description]{task.description}', style=CYAN, justify='left'),
            SpinnerColumn(),
            BarColumn(bar_width=20),
            '[progress.percentage]{task.percentage:>3.1f}%',
            '•',
            DownloadColumn(binary_units=True),
            '•',
            TimeRemainingColumn(),
            transient=True,
        )

    async def _download_video_or_audio(self, filepath: Path, urls: tuple[str], progress: Progress) -> None:
        if filepath.exists():
            if filepath.stat().st_size != 0:
                print(f'[{CYAN}]{filepath} 已存在，跳过')
                return
        if not (await self._download_save(filepath, urls[0], progress)):
            print(f'[{CYAN}]使用备用链接下载')
            await self._download_save(filepath, urls[1], progress)

    async def _download_save(self, filepath: Path, url: str, progress: Progress) -> bool:
        try:
            async with ClientSession(headers=self.headers, timeout=ClientTimeout(self.timeout)) as session:
                async with session.get(URL(url, encoded=True)) as response:
                    task_id = progress.add_task(filepath.name, total=int(response.headers.get('content-length', 0)) or None)
                    with open(filepath, 'wb') as f:
                        async for chunk in response.content.iter_chunked(1024*1024):
                            f.write(chunk)
                            progress.update(task_id, advance=len(chunk))
                    progress.remove_task(task_id)
                    print(f'[{GREEN}]{filepath} 下载完成')
                    return True
        except Exception as e:
            progress.remove_task(task_id)
            filepath.unlink()
            print(f'[{YELLOW}]{e}')
            return False

    def _merge(self, video_path: Path, audio_path: Path, filepath: Path) -> None:
        with self._progress_object_merge() as progress:
            progress.add_task('正在合并音视频', total=None)
            input_video = ffmpeg_input(str(video_path.absolute()))
            input_audio = ffmpeg_input(str(audio_path.absolute()))
            output = ffmpeg_output(input_video, input_audio, str(filepath.absolute()), vcodec='copy', acodec='aac')
            ffmpeg_run(output, quiet=True)
            print(f'[{GREEN}]{filepath} 合并完成')
            video_path.unlink()
            audio_path.unlink()

    @staticmethod
    def _progress_object_merge() -> Progress:
        return Progress(
            TextColumn('[progress.description]{task.description}', style=CYAN, justify='left'),
            '•',
            BarColumn(bar_width=20),
            '•',
            TimeElapsedColumn(),
            transient=True,
        )


def main():
    tips = dedent(
        f'''
        {DELIMITER}
        1. 下载视频
        2. 更新cookie并下载视频
        {DELIMITER}

        请选择运行模式'''
    )

    mode = Prompt.ask(f'[{CYAN}]{tips}', choices=['q', '1', '2'])
    if mode != 'q':
        if mode == '2':
            cookie = Prompt.ask(f'\n[{CYAN}]请输入cookie').strip()
            with open(Path(__file__).with_name('cookie.txt'), 'w') as f:
                f.write(cookie)

        while True:
            url = Prompt.ask(f'\n[{CYAN}]请输入 url').strip()
            if url.lower() == 'q':
                break
            elif not url:
                continue

            DownloadBiLiBiLi(url).run()


if __name__ == '__main__':
    main()
