import json
import scrapy
import time
from w3lib.html import remove_tags
import re
import os
from scrapy.utils.project import get_project_settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('alkoteka.alkoteka_spider')

class AlkotekaSpider(scrapy.Spider):
    name = "spider_name"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        project_settings = get_project_settings()
        self.api_url = project_settings.get("API_URL")
        self.city_uuid = project_settings.get("CITY_UUID")
        self.category_conf = project_settings.get("CATEGORY_CONF")

        try:
            file_path = os.path.join(
                os.path.dirname(__file__),
                "start_urls.txt"
            )
            
            with open(file_path, "r", encoding="utf-8") as f:
                self.start_urls = [
                    self.api_url + self.city_uuid + "&page=1" + self.category_conf +
                    url.replace("https://alkoteka.com/catalog/", "").strip()
                    for url in f.readlines()
                ]
        except FileNotFoundError:
            logger.error("Файл start_urls.txt не найден — используются базовые категории.")
            self.start_urls = [
                self.api_url + self.city_uuid + "&page=1" + self.category_conf + "vino",
                self.api_url + self.city_uuid + "&page=1" + self.category_conf + "krepkiy-alkogol",
                self.api_url + self.city_uuid + "&page=1" + self.category_conf + "slaboalkogolnye-napitki-2",
            ]

    def safe_json(self, response):
        try:
            return json.loads(response.text)
        except Exception:
            self.logger.error(f"Не JSON ответ: {response.url}")
            return None

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(url=url, callback=self.parse)

    def parse(self, response):
        data = self.safe_json(response)
        if not data:
            return

        results = data.get("results")
        if not results:
            self.logger.info(f"Пустая категория: {response.url}")
            return

        for prod in results:
            slug = prod.get("slug")
            if not slug:
                continue

            product_url = f"{self.api_url}/{slug}{self.city_uuid}"
            yield scrapy.Request(product_url, callback=self.parse_product, meta={"slug": slug})

        meta = data.get("meta", {})
        if meta.get("has_more_pages"):
            next_page = meta.get("current_page", 1) + 1
            next_url = response.url.replace(
                f"&page={meta['current_page']}",
                f"&page={next_page}"
            )
            self.logger.info(f"Переход на страницу {next_page}: {next_url}")
            yield scrapy.Request(next_url, callback=self.parse)
        else:
            self.logger.info(f"Категория завершена: {response.url}")

    def parse_product(self, response):
        data = self.safe_json(response)
        if not data:
            return

        product = data.get("results")
        if not product:
            self.logger.error(f"Нет product['results']: {response.url}")
            return

        # Цена
        price_block = product.get("price_details") or None
        if price_block is None:
            original = float(product.get("price") or 0)
            current = original
        else:
            original = float(price_block[0].get("prev_price") or 0)
            current = float(price_block[0].get("price") or original)

        # Brand
        brand = ""
        for block in product.get("description_blocks", []):
            if block.get("code") == "brend":
                brand = block["values"][0]["name"]

        # Описание
        description = ""
        for desc in product.get("text_blocks", []):
            if desc.get("title") == "Описание":
                raw = desc.get("content", "")
                description = self.clean_text(raw)

        # Данные метаданных
        metadata = {"__description": description}

        for block in product.get("description_blocks", []):
            title = block.get("title")
            if not title:
                continue

            if block.get("values"):
                metadata[title] = block["values"][0].get("name")
            elif block.get("min"):
                metadata[title] = block["min"]
            else:
                metadata[title] = "Неизвестные данные"

        # Варианты (объём/крепость)
        variants = len(product.get("filter_labels", []))

        # Объём для title
        volume = ""
        for f in product.get("filter_labels", []):
            if f.get("filter") == "obem":
                volume = f.get("title", "")

        category_slug = product.get("category", {}).get("slug")
        slug = response.meta.get("slug")
        vendor_code = product.get("vendor_code")

        # URL к продукту
        if slug and category_slug:
            product_url = f"https://alkoteka.com/product/{category_slug}/{slug}"
        else:
            product_url = response.url

        item = {
            "timestamp": int(time.time()),
            "RPC": vendor_code,
            "url": product_url,
            "title": f"{product.get('name', '')}, {volume}" if volume else product.get("name", ""),
            "marketing_tags": [
                f.get("title") for f in product.get("filter_labels", [])
                if f.get("filter") in ("dopolnitelno", "tovary-so-skidkoi")
            ],
            "brand": brand,
            "section": [product.get("category", {}).get("name", "")],
            "price_data": {
                "original": original,
                "current": current,
                "sale_tag": f"Скидка {round((original - current) / original * 100)}%"
                if original and current and original != current else ""
            },
            "stock": {
                "in_stock": product.get("available", False),
                "count": product.get("quantity_total", 0),
            },
            "assets": {
                "main_image": product.get("image_url"),
                "set_images": [],
                "view360": [],
                "video": [],
            },
            "metadata": metadata,
            "variants": variants,
        }

        yield item

    def clean_text(self, text):
        if not text:
            return ""

        text = remove_tags(text)
        text = re.sub(r"\s+", " ", text)
        text = text.strip()

        return text