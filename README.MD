# CSV-driven Browser Automation Video Recorder

Инструмент на Python и Playwright, который для каждой строки входного CSV записывает видео автоматизированных действий в браузере: открывает DuckDuckGo, плавно ведёт курсор к полю поиска, посимвольно вводит запрос Magento developer in <city>, нажимает поиск и кликает по первому результату. В видео добавлены SVG-курсор, жёлтая подсветка, ripple при кликах и callout с текстом.

## Требования

- Python 3.9+
- Playwright для Python (sync API), браузер Chromium
- Других сторонних библиотек нет

## Установка

pip install -r requirements.txt
python -m playwright install chromium

## Запуск

python capture_video.py
python capture_video.py --csv input.csv --out ./video

Для каждой строки CSV скрипт печатает путь к сохранённому видео: [1] Saved: <путь>. Видео сохраняются в ./video под именем <Company Name>.webm.

## Формат input.csv

Кодировка UTF-8, разделитель ;, ограничитель полей ". Заголовок:

URL;Company Name;Language;Country;Postcode;City;Street;HouseNo

В запросе используется значение City.

## Структура проекта

| Файл             | Назначение                                                                                   |
| ---------------- | -------------------------------------------------------------------------------------------- |
| capture_video.py | Читает CSV и для каждой строки вызывает qscript.CaptureVideo_Magento(...)                    |
| qscript.py       | Функция CaptureVideo_Magento, глобальная переменная PATH_TO_VIDEO_FOLDER, визуальные эффекты |
| requirements.txt | Зависимости                                                                                  |
| input.csv        | Пример входных данных                                                                        |

Функция CaptureVideo_Magento возвращает абсолютный путь к .webm или None при сбое.

## Настройки через переменные окружения

Все необязательны.

| Переменная                           | Значение                                                   |
| ------------------------------------ | ---------------------------------------------------------- |
| QS_HEADLESS=1                        | Запуск без окна браузера (по умолчанию окно видно)         |
| QS_BROWSER_CHANNEL=chrome или msedge | Использовать установленный Chrome или Edge вместо Chromium |
| QS_DEBUG=1                           | При сбое сохранять скриншот и видео неудачной записи       |

Пример для PowerShell:

$env:QS_HEADLESS="1"
python capture_video.py

## Примечания

- По умолчанию браузер открывается с окном. Для сервера или компьютера без графической среды используйте QS_HEADLESS=1 или xvfb-run -a python capture_video.py на Linux.
- DuckDuckGo может блокировать автоматические запросы (страница ошибки 418), особенно в режиме без окна или при частых запусках. В этом случае подождите, смените сеть или запустите с видимым окном.
- Скрипт рассчитан на текущие селекторы DuckDuckGo, они заданы константами в начале qscript.py.
