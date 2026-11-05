import math
import os
import random
import re
import shutil
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

PATH_TO_VIDEO_FOLDER = "./video"

VIDEO_SIZE = {"width": 1280, "height": 720}

SEL_SEARCH_BOX = 'textarea[name="q"]'
SEL_SEARCH_BUTTON = 'form button[type="submit"]'
SEL_FIRST_RESULT = 'a[data-testid="result-title-a"]'

_script_path = os.path.join(os.path.dirname(file), "cursor_script.js")
with open(_script_path, "r", encoding="utf-8") as _f:
    INIT_SCRIPT = _f.read()


def CaptureVideo_Magento(
    url,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
):
    ...
