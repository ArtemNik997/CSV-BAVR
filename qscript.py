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
