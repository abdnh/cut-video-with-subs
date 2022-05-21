.PHONY: all bundle run forms fix mypy pylint clean

# NOTE: most of these build steps assume Windows

APP_NAME := cut-video-with-subs
BIN := dist/$(APP_NAME).exe

all: bundle

forms: src/forms/form.py

src/forms/%.py: designer/%.ui
	pyuic6 $^ > $@

ffmpeg: dist/$(APP_NAME)/bin

bin/ffmpeg.7z:
	curl -L https://www.gyan.dev/ffmpeg/builds/ffmpeg-git-essentials.7z -o $@

dist/$(APP_NAME)/bin: bin/ffmpeg.7z
	7z x bin/ffmpeg.7z -o$@ -r */bin/*
	mv $@/ffmpeg*/**/** $@
	# we don't need ffplay
	rm $@/ffplay.exe
	find $@/* -type d -delete

bundle: $(BIN) forms

$(BIN): src/*.py
	pyinstaller src/main.py --name=$(APP_NAME) --icon=icon.ico --windowed

run: bundle
	./$(BIN)

fix:
	python -m black src --exclude=forms
	python -m isort src

mypy:
	python -m mypy .

pylint:
	python -m pylint src

clean:
	rm -rf build/ dist/
