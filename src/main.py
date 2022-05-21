import math
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path
from typing import Any, List, Optional

import srt
from PyQt6.QtCore import Qt, QTime
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
)

from forms.form import Ui_Dialog
from taskman import TaskManager


def get_exe_path(cmd: str) -> str:
    """Get the full path of the executable `cmd` in path, otherwise assume we bundle a copy of it under `./bin` and return its path."""
    path = shutil.which(cmd)
    if not path:
        path = shutil.which(f"./bin/{cmd}")
    return path if path else cmd


def format_time(seconds: float) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    seconds, milliseconds = divmod(seconds, 1)
    milliseconds *= 1000
    formatted = ""
    for label, num in {
        "h": hours,
        "m": minutes,
        "s": seconds,
        "ms": milliseconds,
    }.items():
        num = int(num)
        # TODO: do not include unnecessary parts
        formatted += f"{num:02d}{label}"
    return formatted


def should_include_sub(sub: srt.Subtitle, start: timedelta, end: timedelta) -> bool:
    return start <= sub.start <= end or sub.start <= start <= sub.end


def get_split_subs(
    subtitles: List[srt.Subtitle], start_s: float, end_s: float
) -> List[srt.Subtitle]:
    split: List[srt.Subtitle] = []
    start = timedelta(seconds=start_s)
    end = timedelta(seconds=end_s)
    for sub in subtitles:
        if should_include_sub(sub, start, end):
            new_sub = srt.Subtitle(
                len(split) + 1,
                max(timedelta(seconds=0), sub.start - start),
                sub.end - start,
                sub.content,
            )
            split.append(new_sub)
        elif sub.start >= end:
            break
    return split


def startup_info() -> Any:
    if sys.platform != "win32":
        return None
    info = subprocess.STARTUPINFO()  # pytype: disable=module-attr
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # pytype: disable=module-attr
    return info


class Dialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.taskman = TaskManager()
        self.form = Ui_Dialog()
        self.form.setupUi(self)
        self.setup_ui()
        self.video_file: Optional[Path] = None
        self.sub_file: Optional[Path] = None
        self.out_folder: Optional[Path] = None
        self.video_duration: float = 0

    def setup_ui(self) -> None:
        self.form.chooseVideoButton.clicked.connect(self.on_choose_video)  # type: ignore
        self.form.chooseSubtitleButton.clicked.connect(self.on_choose_subtitle)  # type: ignore
        self.form.chooseFolderButton.clicked.connect(self.on_choose_folder)  # type: ignore
        self.form.processButton.clicked.connect(self.on_process)  # type: ignore

    def on_choose_video(self) -> None:
        video_path = QFileDialog.getOpenFileName(dialog)[0]
        if not video_path:
            return
        self.form.videoPathLabel.setText(video_path)
        self.video_file = Path(video_path)
        if self.video_file:
            self.sub_file = Path(self.video_file).with_suffix(".srt")
            if self.sub_file.exists():
                self.form.subtitlePathLabel.setText(str(self.sub_file))
            self.video_duration = self.get_video_duration()
            # split video into 4 parts by default
            clip_length = QTime(0, 0, 0).addSecs(int(self.video_duration) // 4)
            self.form.durationTimeEdit.setTime(clip_length)

    def on_choose_subtitle(self) -> None:
        sub_path = QFileDialog.getOpenFileName(dialog, filter="Subtitles (*.srt)")[0]
        if sub_path:
            self.sub_file = Path(sub_path)
            self.form.subtitlePathLabel.setText(sub_path)

    def on_choose_folder(self) -> None:
        self.out_folder = Path(QFileDialog.getExistingDirectory(dialog))
        if self.out_folder:
            self.form.outputPathLabel.setText(str(self.out_folder))

    def on_process(self) -> None:
        if not self.video_file:
            QMessageBox.warning(dialog, "Warning", "You have not chosen a video file!")
            return
        if not self.sub_file:
            QMessageBox.warning(
                dialog, "Warning", "You have not chosen a subtitle file!"
            )
            return
        if not self.out_folder:
            QMessageBox.warning(
                dialog, "Warning", "You have not chosen an output folder!"
            )
            return
        self.cut_video()

    def get_video_duration(self) -> float:
        outb = subprocess.check_output(
            [get_exe_path("ffprobe"), "-i", self.video_file, "-show_format"],
            startupinfo=startup_info(),
        )
        out = outb.decode(encoding="utf-8")
        match = re.search(r"duration=(.*)\n", out)
        secs = float(match.group(1))
        return secs

    def cut_video(self) -> None:
        length_qtime = self.form.durationTimeEdit.time()
        length = (
            length_qtime.hour() * 3600
            + length_qtime.minute() * 60
            + length_qtime.second()
        )
        video_count = int(math.ceil(self.video_duration / length))
        max_progress = int(self.video_duration)
        progress = QProgressDialog(
            "Generating videos...", "Cancel", 0, max_progress, self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress_bar = QProgressBar(progress)
        progress_bar.setRange(0, max_progress)
        progress_bar.setValue(0)
        progress.setBar(progress_bar)
        progress.setMinimumWidth(350)
        progress.setMinimumDuration(0)

        def task() -> None:
            with open(self.sub_file, "r", encoding="utf-8") as file:
                subtitles = list(srt.parse(file.read()))
            start = 0
            vid_i = 0
            canceled = False

            def check_cancel() -> None:
                if progress.wasCanceled():
                    nonlocal canceled
                    canceled = True

            def update_progress() -> None:
                progress_bar.setValue(start)
                progress.setLabelText(
                    f"Generating video {vid_i} out of {video_count}..."
                )

            while start <= self.video_duration:
                vid_i += 1
                self.taskman.run_on_main(update_progress)
                end = float(start + length)
                if end > self.video_duration:
                    end = self.video_duration
                start_fmt = format_time(start)
                end_fmt = format_time(end)
                base, ext = os.path.splitext(os.path.basename(self.video_file))
                base_name = os.path.join(
                    self.out_folder, f"{base}_{start_fmt}-{end_fmt}"
                )
                split_video_name = base_name + ext
                srt_name = base_name + ".srt"

                # generate retimed subs
                retimed_subs = get_split_subs(subtitles, start, end)
                with open(srt_name, "w", encoding="utf-8") as file:
                    file.write(srt.compose(retimed_subs))

                # generate clip

                with subprocess.Popen(
                    [
                        get_exe_path("ffmpeg"),
                        "-y",
                        "-i",
                        self.video_file,
                        # "-vcodec",
                        # "copy",
                        # "-acodec",
                        # "copy",
                        "-ss",
                        str(start),
                        "-to",
                        str(end),
                        split_video_name,
                    ],
                    startupinfo=startup_info(),
                ) as proc:
                    while proc.poll() is None:
                        self.taskman.run_on_main(check_cancel)
                        if canceled:
                            proc.kill()
                        time.sleep(1)

                start += length

        def on_done(fut: Future) -> None:
            try:
                fut.result()
                self.form.statusLabel.setText("Done!")
            except Exception as exc:
                QMessageBox.warning(dialog, "Error", str(exc))
            finally:
                progress_bar.setValue(max_progress)
                progress.close()

        self.taskman.run_in_background(task, on_done)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dialog = Dialog()
    dialog.exec()
