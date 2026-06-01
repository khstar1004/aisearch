#!/usr/bin/env python3
import argparse
import json
import math
import pathlib
import statistics
import time
import urllib.request


DEFAULT_QWEN_URL = "http://localhost:8111"
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent / "results"


PROMO_DOCS = [
    {
        "id": "pen-bic-set",
        "category": "pen",
        "title": "\ub85c\uace0 \uc778\uc1c4\uc6a9 \ubcfc\ud39c \uc138\ud2b8",
        "tags": ["\ubcfc\ud39c", "\ud544\uae30\uad6c", "\uc800\uac00\ud615", "\ub85c\uace0\uc778\uc1c4"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/4_Bic_Cristal_pens_and_caps.jpg/330px-4_Bic_Cristal_pens_and_caps.jpg",
    },
    {
        "id": "pen-green",
        "category": "pen",
        "title": "\ucd08\ub85d\uc0c9 \ud310\ucd09\uc6a9 \ubcfc\ud39c",
        "tags": ["\ubcfc\ud39c", "\uc0c9\uc0c1\ubcfc\ud39c", "\ud544\uae30\uad6c"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/BIC_Cristal_Soft_-_Green.jpg/330px-BIC_Cristal_Soft_-_Green.jpg",
    },
    {
        "id": "pen-red",
        "category": "pen",
        "title": "\ube68\uac04\uc0c9 \ud310\ucd09\uc6a9 \ubcfc\ud39c",
        "tags": ["\ubcfc\ud39c", "\uc0c9\uc0c1\ubcfc\ud39c", "\ud544\uae30\uad6c"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/39/BIC_Cristal_Soft_-_Red.jpg/330px-BIC_Cristal_Soft_-_Red.jpg",
    },
    {
        "id": "mug-kfc",
        "category": "mug",
        "title": "\ud310\ucd09\uc6a9 \ucee4\ud53c \uba38\uadf8\ucef5",
        "tags": ["\uba38\uadf8\ucef5", "\ucee4\ud53c\ucef5", "\uae30\ub150\ud488", "\ub85c\uace0\uc778\uc1c4"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0f/KFC_Coffee_Mug%2C_Dhaka.jpg/330px-KFC_Coffee_Mug%2C_Dhaka.jpg",
    },
    {
        "id": "mug-coffee",
        "category": "mug",
        "title": "\ucee4\ud53c\uc20d \ub85c\uace0 \uba38\uadf8\ucef5",
        "tags": ["\uba38\uadf8", "\ucee4\ud53c", "\uc138\ub77c\ubbf9"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Coffee_selections_and_fresh_brewed_cup.jpg/330px-Coffee_selections_and_fresh_brewed_cup.jpg",
    },
    {
        "id": "bottle-job",
        "category": "bottle",
        "title": "\ub85c\uace0 \uc778\uc1c4 \ud150\ube14\ub7ec \ubb3c\ubcd1",
        "tags": ["\ud150\ube14\ub7ec", "\ubb3c\ubcd1", "\ubc14\uc774\ud2c0", "\ud658\uacbd\uce5c\ud654"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/JOB_water_bottle_%28cropped1%29.jpg/330px-JOB_water_bottle_%28cropped1%29.jpg",
    },
    {
        "id": "bottle-festival",
        "category": "bottle",
        "title": "\ud589\uc0ac \uae30\ub150\ud488 \ubb3c\ubcd1",
        "tags": ["\ud150\ube14\ub7ec", "\ubb3c\ubcd1", "\uc2a4\ud3ec\uce20\ubc14\uc774\ud2c0"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/37/Montclair_Film_Festival_Water_Bottle.jpg/330px-Montclair_Film_Festival_Water_Bottle.jpg",
    },
    {
        "id": "umbrella-folding",
        "category": "umbrella",
        "title": "\uc811\uc774\uc2dd \uc6b0\uc0b0 \ud310\ucd09\ubb3c",
        "tags": ["\uc6b0\uc0b0", "\uc811\uc774\uc2dd\uc6b0\uc0b0", "\uc7a5\ub9c8", "\ub85c\uace0\uc778\uc1c4"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/42/Tumella_umbrella_-_Moonlight_design.png/330px-Tumella_umbrella_-_Moonlight_design.png",
    },
    {
        "id": "umbrella-cocktail",
        "category": "umbrella",
        "title": "\uc791\uc740 \uc6b0\uc0b0 \ub514\uc790\uc778 \uc18c\ud488",
        "tags": ["\uc6b0\uc0b0", "\ubbf8\ub2c8\uc6b0\uc0b0", "\uc774\ubca4\ud2b8\uc18c\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5b/Cocktail_umbrella_side.jpg/330px-Cocktail_umbrella_side.jpg",
    },
    {
        "id": "tote-logo",
        "category": "tote_bag",
        "title": "\ub85c\uace0 \uc778\uc1c4 \uc5d0\ucf54\ubc31",
        "tags": ["\uc5d0\ucf54\ubc31", "\uce94\ubc84\uc2a4\ubc31", "\uc1fc\ud551\ubc31", "\ud310\ucd09\ubb3c"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Last_Drip_Designs_Tote_Bag.png/330px-Last_Drip_Designs_Tote_Bag.png",
    },
    {
        "id": "tote-canvas",
        "category": "tote_bag",
        "title": "\uce94\ubc84\uc2a4 \ud1a0\ud2b8\ubc31",
        "tags": ["\uc5d0\ucf54\ubc31", "\ud1a0\ud2b8\ubc31", "\ucc9c\uac00\ubc29"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/94/Canvas_tote_bag_from_Books_%26_Books%2C_Miami%2C_Florida%2C_USA_-_20130912.jpg/330px-Canvas_tote_bag_from_Books_%26_Books%2C_Miami%2C_Florida%2C_USA_-_20130912.jpg",
    },
    {
        "id": "usb-industrial",
        "category": "usb",
        "title": "\ub85c\uace0 \uc778\uc1c4 USB \uba54\ubaa8\ub9ac",
        "tags": ["USB", "\uba54\ubaa8\ub9ac", "\uc800\uc7a5\uc7a5\uce58", "\ud310\ucd09\ubb3c"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/5/52/16876_-_Swissbit_Industrial_USB_Flash_Drive_-_16GB_%28cropped%29.jpg",
    },
    {
        "id": "usb-card-reader",
        "category": "usb",
        "title": "\uae30\uc5c5 \ud64d\ubcf4\uc6a9 USB \uba54\ubaa8\ub9ac",
        "tags": ["USB", "\ud50c\ub798\uc2dc\ub4dc\ub77c\uc774\ube0c", "\uae30\ub150\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/USB_Flash_Drive_and_Card_Reader.jpg/330px-USB_Flash_Drive_and_Card_Reader.jpg",
    },
    {
        "id": "towel-roll",
        "category": "towel",
        "title": "\ud638\ud154\uc2dd \ud0c0\uc6d4 \ub2f5\ub840\ud488",
        "tags": ["\ud0c0\uc6d4", "\uc218\uac74", "\ub2f5\ub840\ud488", "\uc0dd\ud65c\uc6a9\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1f/Roll_of_Paper_Towels.jpg/330px-Roll_of_Paper_Towels.jpg",
    },
    {
        "id": "towel-paper",
        "category": "towel",
        "title": "\ud398\uc774\ud37c \ud0c0\uc6d4 \uc0dd\ud65c\uc6a9\ud488",
        "tags": ["\ud0c0\uc6d4", "\ud398\uc774\ud37c\ud0c0\uc6d4", "\uc704\uc0dd\uc6a9\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9d/Paper_towel.png/330px-Paper_towel.png",
    },
    {
        "id": "notebook-composition",
        "category": "notebook",
        "title": "\ud310\ucd09\uc6a9 \ub178\ud2b8 \ub2e4\uc774\uc5b4\ub9ac",
        "tags": ["\ub178\ud2b8", "\ub2e4\uc774\uc5b4\ub9ac", "\ubb38\uad6c", "\uc624\ud53c\uc2a4"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/42/Composition_Notebook_%285736841980%29.jpg/330px-Composition_Notebook_%285736841980%29.jpg",
    },
    {
        "id": "notebook-campus",
        "category": "notebook",
        "title": "\ucea0\ud37c\uc2a4 \ub178\ud2b8 \ubb38\uad6c \ud310\ucd09\ubb3c",
        "tags": ["\ub178\ud2b8", "\ubb38\uad6c", "\ud559\uc0dd\uc6a9\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/13/Kokuyo_Campus_note_at_Niconico_chokaigi_3_in_2014.jpg/330px-Kokuyo_Campus_note_at_Niconico_chokaigi_3_in_2014.jpg",
    },
    {
        "id": "lanyard-wiki",
        "category": "lanyard",
        "title": "\ud589\uc0ac\uc6a9 \ub85c\uace0 \ub79c\uc57c\ub4dc",
        "tags": ["\ub79c\uc57c\ub4dc", "\ubaa9\uac78\uc774", "\uba85\ucc30", "\uc774\ubca4\ud2b8"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/WMDE-Give-Aways_Lanyard_mit_Wikipedia-Logo.jpg/330px-WMDE-Give-Aways_Lanyard_mit_Wikipedia-Logo.jpg",
    },
    {
        "id": "lanyard-linuxday",
        "category": "lanyard",
        "title": "\ucee8\ud37c\ub7f0\uc2a4 \ub79c\uc57c\ub4dc",
        "tags": ["\ub79c\uc57c\ub4dc", "\uba85\ucc30\uc904", "\uae30\ub150\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f8/LinuxDay_Milano_2022_gadget_lanyard.jpg/330px-LinuxDay_Milano_2022_gadget_lanyard.jpg",
    },
    {
        "id": "keychain-pink",
        "category": "keychain",
        "title": "\ud0a4\ub9c1 \uc5f4\uc1e0\uace0\ub9ac \uc18c\ud488",
        "tags": ["\ud0a4\ub9c1", "\uc5f4\uc1e0\uace0\ub9ac", "\uc18c\ud488", "\uae30\ub150\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/Pink_keychain_razor_folded.jpg/330px-Pink_keychain_razor_folded.jpg",
    },
    {
        "id": "keychain-car",
        "category": "keychain",
        "title": "\uae30\uc5c5 \ud64d\ubcf4\uc6a9 \uba54\ud0c8 \ud0a4\ud640\ub354",
        "tags": ["\ud0a4\ub9c1", "\ud0a4\ud640\ub354", "\uc5f4\uc1e0", "\uba54\ud0c8"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Lada_110_keys.jpg/330px-Lada_110_keys.jpg",
    },
    {
        "id": "calendar-1952",
        "category": "calendar",
        "title": "\ud0c1\uc0c1 \ub2ec\ub825 \ud310\ucd09\ubb3c",
        "tags": ["\ub2ec\ub825", "\uce98\ub9b0\ub354", "\uc624\ud53c\uc2a4", "\uc5f0\ub9d0\uc120\ubb3c"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bd/1952_Gay_Products_Company_calendar_-_If_it%27s_Gay_it%27s_okay.png/330px-1952_Gay_Products_Company_calendar_-_If_it%27s_Gay_it%27s_okay.png",
    },
    {
        "id": "calendar-wp20",
        "category": "calendar",
        "title": "\ud589\uc0ac \uae30\ub150 \uce98\ub9b0\ub354",
        "tags": ["\ub2ec\ub825", "\uce98\ub9b0\ub354", "\uc0ac\uc740\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/55/WP20_Taschenfeitel_Wikipedia_Lanyard_Kalender.jpg/330px-WP20_Taschenfeitel_Wikipedia_Lanyard_Kalender.jpg",
    },
    {
        "id": "mousepad-samsung",
        "category": "mousepad",
        "title": "\ub85c\uace0 \uc778\uc1c4 \ub9c8\uc6b0\uc2a4\ud328\ub4dc",
        "tags": ["\ub9c8\uc6b0\uc2a4\ud328\ub4dc", "\ucef4\ud4e8\ud130\uc6a9\ud488", "\uc624\ud53c\uc2a4"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/Samsung_mouse_and_mousepad_20060822.jpg/330px-Samsung_mouse_and_mousepad_20060822.jpg",
    },
    {
        "id": "mousepad-wikipedia",
        "category": "mousepad",
        "title": "\uae30\uc5c5 \ub85c\uace0 \ub9c8\uc6b0\uc2a4\ud328\ub4dc",
        "tags": ["\ub9c8\uc6b0\uc2a4\ud328\ub4dc", "\uc0ac\ubb34\uc6a9\ud488", "\ud64d\ubcf4\ubb3c"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Wikipedia_logo_mousepad_%2801%29.jpg/330px-Wikipedia_logo_mousepad_%2801%29.jpg",
    },
    {
        "id": "sticky-notes",
        "category": "sticky_notes",
        "title": "\ud3ec\uc2a4\ud2b8\uc787 \uba54\ubaa8\uc9c0 \ud310\ucd09\ubb3c",
        "tags": ["\ud3ec\uc2a4\ud2b8\uc787", "\uba54\ubaa8\uc9c0", "\uc810\ucc29\uba54\ubaa8", "\uc0ac\ubb34\uc6a9\ud488"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Sticky_notes.jpg/330px-Sticky_notes.jpg",
    },
    {
        "id": "sticky-postit-board",
        "category": "sticky_notes",
        "title": "\uc0ac\ubb34\uc2e4 \uc810\ucc29 \uba54\ubaa8\uc9c0",
        "tags": ["\ud3ec\uc2a4\ud2b8\uc787", "\uba54\ubaa8", "\ubb38\uad6c"],
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ae/ESEAP_Strategy_Summit_Day_1_-_Post-it_notes_for_Product_and_Technology_Thematic_Area.jpg/330px-ESEAP_Strategy_Summit_Day_1_-_Post-it_notes_for_Product_and_Technology_Thematic_Area.jpg",
    },
]


TEXT_QUERIES = [
    {"query": "\ub85c\uace0 \uc778\uc1c4 \ubcfc\ud39c", "category": "pen"},
    {"query": "\ud310\ucd09\uc6a9 \uba38\uadf8\ucef5", "category": "mug"},
    {"query": "\ud150\ube14\ub7ec \ubb3c\ubcd1", "category": "bottle"},
    {"query": "\uc811\uc774\uc2dd \uc6b0\uc0b0 \ud310\ucd09\ubb3c", "category": "umbrella"},
    {"query": "\uc5d0\ucf54\ubc31 \uce94\ubc84\uc2a4 \uac00\ubc29", "category": "tote_bag"},
    {"query": "USB \uba54\ubaa8\ub9ac", "category": "usb"},
    {"query": "\uc218\uac74 \ud0c0\uc6d4 \ub2f5\ub840\ud488", "category": "towel"},
    {"query": "\ub178\ud2b8 \ub2e4\uc774\uc5b4\ub9ac", "category": "notebook"},
    {"query": "\ubaa9\uac78\uc774 \uba85\ucc30 \ub79c\uc57c\ub4dc", "category": "lanyard"},
    {"query": "\uc5f4\uc1e0\uace0\ub9ac \ud0a4\ub9c1", "category": "keychain"},
    {"query": "\ud0c1\uc0c1 \ub2ec\ub825", "category": "calendar"},
    {"query": "\ub9c8\uc6b0\uc2a4\ud328\ub4dc", "category": "mousepad"},
    {"query": "\ud3ec\uc2a4\ud2b8\uc787 \uba54\ubaa8\uc9c0", "category": "sticky_notes"},
]


IMAGE_URL_OVERRIDES = {
    "pen-bic-set": "https://loremflickr.com/cache/resized/65535_51538586319_7d90508552_360_360_nofilter.jpg",
    "pen-green": "https://loremflickr.com/cache/resized/65535_50826967657_2d91de943d_z_360_360_nofilter.jpg",
    "pen-red": "https://loremflickr.com/cache/resized/65535_54482185059_7bbda1ebab_360_360_nofilter.jpg",
    "mug-kfc": "https://loremflickr.com/cache/resized/65535_54561195997_fb40081b3b_z_360_360_nofilter.jpg",
    "mug-coffee": "https://loremflickr.com/cache/resized/65535_54555810262_d15f52c8df_c_360_360_nofilter.jpg",
    "bottle-job": "https://loremflickr.com/cache/resized/65535_54532387234_728d45897c_z_360_360_nofilter.jpg",
    "bottle-festival": "https://loremflickr.com/cache/resized/65535_54575656664_b36ff44f14_360_360_nofilter.jpg",
    "umbrella-folding": "https://loremflickr.com/cache/resized/65535_54573126452_727503abf1_360_360_nofilter.jpg",
    "umbrella-cocktail": "https://loremflickr.com/cache/resized/65535_54563378544_2d9351f136_c_360_360_nofilter.jpg",
    "tote-logo": "https://loremflickr.com/cache/resized/65535_54465270873_d4e08dd114_360_360_nofilter.jpg",
    "tote-canvas": "https://loremflickr.com/cache/resized/65535_54465364700_7fc75f8e54_360_360_nofilter.jpg",
    "usb-industrial": "https://loremflickr.com/cache/resized/4426_37060628050_843c9f0211_z_360_360_nofilter.jpg",
    "usb-card-reader": "https://loremflickr.com/cache/resized/65535_48271169332_5a94128909_360_360_nofilter.jpg",
    "towel-roll": "https://loremflickr.com/cache/resized/65535_53986691439_b00f1e8058_360_360_nofilter.jpg",
    "towel-paper": "https://loremflickr.com/cache/resized/65535_53986374176_bd75e1b018_360_360_nofilter.jpg",
    "notebook-composition": "https://loremflickr.com/cache/resized/65535_54064995949_79b2f4515a_360_360_nofilter.jpg",
    "notebook-campus": "https://loremflickr.com/cache/resized/65535_54546402260_6a196bef0a_z_360_360_nofilter.jpg",
    "lanyard-wiki": "https://loremflickr.com/cache/resized/65535_52498344892_91fd108c5f_z_360_360_nofilter.jpg",
    "lanyard-linuxday": "https://loremflickr.com/cache/resized/65535_52332367042_aeec921dfd_z_360_360_nofilter.jpg",
    "keychain-pink": "https://loremflickr.com/cache/resized/65535_54501379846_e24fe50d0a_z_360_360_nofilter.jpg",
    "keychain-car": "https://loremflickr.com/cache/resized/5119_5813255149_9fec1c5c40_z_360_360_nofilter.jpg",
    "calendar-1952": "https://loremflickr.com/cache/resized/65535_54452587528_1849b7c51e_z_360_360_nofilter.jpg",
    "calendar-wp20": "https://loremflickr.com/cache/resized/65535_54452339331_185c7c4302_z_360_360_nofilter.jpg",
    "mousepad-samsung": "https://loremflickr.com/cache/resized/65535_52055982224_d382afdb10_c_360_360_nofilter.jpg",
    "mousepad-wikipedia": "https://loremflickr.com/cache/resized/65535_52055982339_989c88664a_360_360_nofilter.jpg",
    "sticky-notes": "https://loremflickr.com/cache/resized/5312_14104323927_4c7ed71983_360_360_nofilter.jpg",
    "sticky-postit-board": "https://loremflickr.com/cache/resized/65535_53577357492_520aeee2f8_z_360_360_nofilter.jpg",
}


def active_docs():
    docs = []
    for doc in PROMO_DOCS:
        copied = dict(doc)
        if copied["id"] in IMAGE_URL_OVERRIDES:
            copied["image_url"] = IMAGE_URL_OVERRIDES[copied["id"]]
        docs.append(copied)
    return docs


def doc_text(doc):
    return "\n".join([doc["title"], doc["category"], " ".join(doc["tags"])])


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def post_embed(qwen_url, inputs, prompt=None, batch_size=8, timeout=1800):
    vectors = []
    elapsed = 0.0
    for batch in chunks(inputs, batch_size):
        payload = {"inputs": batch}
        if prompt is not None:
            payload["prompt"] = prompt
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            qwen_url.rstrip("/") + "/embed",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        vectors.extend(data["embeddings"])
        elapsed += float(data.get("elapsedMs", 0.0))
    return vectors, round(elapsed, 1)


def dot(lhs, rhs):
    return sum(a * b for a, b in zip(lhs, rhs))


def rank_by_scores(scores):
    return sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)


def reciprocal_rank(order, relevant):
    for rank, idx in enumerate(order, start=1):
        if idx in relevant:
            return 1.0 / rank
    return 0.0


def recall_at(order, relevant, k):
    return 1.0 if any(idx in relevant for idx in order[:k]) else 0.0


def precision_at(order, relevant, k):
    if not order:
        return 0.0
    return sum(1 for idx in order[:k] if idx in relevant) / min(k, len(order))


def average(values):
    return round(sum(values) / len(values), 4) if values else 0.0


def evaluate_text(docs, query_vectors, doc_vector_sets):
    relevant_by_category = {
        query["category"]: {idx for idx, doc in enumerate(docs) if doc["category"] == query["category"]}
        for query in TEXT_QUERIES
    }
    results = {}

    candidates = {
        "text_vector_only": lambda q_idx: [
            dot(query_vectors[q_idx], vector) for vector in doc_vector_sets["text"]
        ],
        "image_vector_cross_modal": lambda q_idx: [
            dot(query_vectors[q_idx], vector) for vector in doc_vector_sets["image"]
        ],
        "multimodal_text_image_vector": lambda q_idx: [
            dot(query_vectors[q_idx], vector) for vector in doc_vector_sets["multimodal"]
        ],
    }

    for weight in [0.25, 0.5, 0.75]:
        candidates[f"fused_text_{weight:.2f}_image_{1 - weight:.2f}"] = (
            lambda q_idx, w=weight: [
                w * dot(query_vectors[q_idx], text_vector)
                + (1 - w) * dot(query_vectors[q_idx], image_vector)
                for text_vector, image_vector in zip(doc_vector_sets["text"], doc_vector_sets["image"])
            ]
        )

    for name, scorer in candidates.items():
        mrr = []
        r1 = []
        r3 = []
        r5 = []
        p5 = []
        examples = []
        for q_idx, query in enumerate(TEXT_QUERIES):
            relevant = relevant_by_category[query["category"]]
            order = rank_by_scores(scorer(q_idx))
            mrr.append(reciprocal_rank(order, relevant))
            r1.append(recall_at(order, relevant, 1))
            r3.append(recall_at(order, relevant, 3))
            r5.append(recall_at(order, relevant, 5))
            p5.append(precision_at(order, relevant, 5))
            examples.append(
                {
                    "query": query["query"],
                    "category": query["category"],
                    "top5": [
                        {
                            "id": docs[idx]["id"],
                            "category": docs[idx]["category"],
                            "title": docs[idx]["title"],
                        }
                        for idx in order[:5]
                    ],
                }
            )
        results[name] = {
            "mrr": average(mrr),
            "recall@1": average(r1),
            "recall@3": average(r3),
            "recall@5": average(r5),
            "precision@5": average(p5),
            "examples": examples,
        }
    return results


def evaluate_image(docs, doc_vector_sets):
    results = {}
    candidates = {
        "image_vector_only": doc_vector_sets["image"],
        "multimodal_text_image_vector": doc_vector_sets["multimodal"],
        "text_vector_only": doc_vector_sets["text"],
    }
    for name, candidate_vectors in candidates.items():
        exact_at_1 = []
        category_at_5 = []
        mrr_self = []
        self_scores = []
        for query_idx, query_vector in enumerate(doc_vector_sets["image"]):
            scores = [dot(query_vector, vector) for vector in candidate_vectors]
            order = rank_by_scores(scores)
            exact_at_1.append(1.0 if order[0] == query_idx else 0.0)
            mrr_self.append(reciprocal_rank(order, {query_idx}))
            category = docs[query_idx]["category"]
            relevant_category = {idx for idx, doc in enumerate(docs) if doc["category"] == category}
            category_at_5.append(recall_at(order, relevant_category, 5))
            self_scores.append(scores[query_idx])
        results[name] = {
            "exact@1": average(exact_at_1),
            "self_mrr": average(mrr_self),
            "category_recall@5": average(category_at_5),
            "mean_self_score": round(statistics.mean(self_scores), 4),
            "min_self_score": round(min(self_scores), 4),
        }
    return results


def best_by(results, metric):
    return max(results.items(), key=lambda item: item[1][metric])


def write_report(path, payload):
    text_best_name, text_best = best_by(payload["text_search"], "mrr")
    image_best_name, image_best = best_by(payload["image_search"], "exact@1")
    lines = [
        "# Promo Search Experiment",
        "",
        f"- Model: `{payload['model']}`",
        f"- Documents: `{payload['document_count']}`",
        f"- Text queries: `{len(TEXT_QUERIES)}`",
        f"- Best text architecture: `{text_best_name}` "
        f"(MRR {text_best['mrr']}, R@1 {text_best['recall@1']}, P@5 {text_best['precision@5']})",
        f"- Best image architecture: `{image_best_name}` "
        f"(Exact@1 {image_best['exact@1']}, category R@5 {image_best['category_recall@5']}, "
        f"mean self score {image_best['mean_self_score']})",
        "",
        "## Text Search",
        "",
        "| architecture | MRR | R@1 | R@3 | R@5 | P@5 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in sorted(payload["text_search"].items()):
        lines.append(
            f"| `{name}` | {metrics['mrr']} | {metrics['recall@1']} | "
            f"{metrics['recall@3']} | {metrics['recall@5']} | {metrics['precision@5']} |"
        )
    lines += [
        "",
        "## Image Search",
        "",
        "| architecture | Exact@1 | Self MRR | Category R@5 | Mean self score | Min self score |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in sorted(payload["image_search"].items()):
        lines.append(
            f"| `{name}` | {metrics['exact@1']} | {metrics['self_mrr']} | "
            f"{metrics['category_recall@5']} | {metrics['mean_self_score']} | "
            f"{metrics['min_self_score']} |"
        )
    lines += [
        "",
        "## Recommendation",
        "",
        "Use a split-vector index for production: Korean product metadata in a text vector field, "
        "product images in a separate image vector field, and route text and image searches to the "
        "matching field. Add score fusion or a reranker only after collecting click/search logs.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qwen-url", default=DEFAULT_QWEN_URL)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    docs = active_docs()
    qwen_url = args.qwen_url.rstrip("/")

    query_vectors, query_ms = post_embed(
        qwen_url,
        [{"text": query["query"]} for query in TEXT_QUERIES],
        prompt="Retrieve relevant ecommerce product images for the user query.",
        batch_size=8,
    )
    text_vectors, text_doc_ms = post_embed(
        qwen_url,
        [{"text": doc_text(doc)} for doc in docs],
        batch_size=16,
    )
    image_vectors, image_doc_ms = post_embed(
        qwen_url,
        [{"image": doc["image_url"]} for doc in docs],
        batch_size=4,
    )
    multimodal_vectors, multimodal_doc_ms = post_embed(
        qwen_url,
        [{"text": doc_text(doc), "image": doc["image_url"]} for doc in docs],
        prompt="Represent the ecommerce product for multilingual image and product search.",
        batch_size=4,
    )

    vector_sets = {
        "text": text_vectors,
        "image": image_vectors,
        "multimodal": multimodal_vectors,
    }
    payload = {
        "model": "Qwen/Qwen3-VL-Embedding-2B",
        "qwenUrl": qwen_url,
        "document_count": len(docs),
        "timingMs": {
            "queryTextVectors": query_ms,
            "docTextVectors": text_doc_ms,
            "docImageVectors": image_doc_ms,
            "docMultimodalVectors": multimodal_doc_ms,
            "totalWall": round((time.perf_counter() - started) * 1000, 1),
        },
        "text_search": evaluate_text(docs, query_vectors, vector_sets),
        "image_search": evaluate_image(docs, vector_sets),
    }

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "promo_eval_results.json"
    report_path = output_dir / "promo_eval_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(report_path, payload)

    text_best_name, text_best = best_by(payload["text_search"], "mrr")
    image_best_name, image_best = best_by(payload["image_search"], "exact@1")
    print(f"wrote {json_path}")
    print(f"wrote {report_path}")
    print(
        f"best text={text_best_name} mrr={text_best['mrr']} "
        f"r1={text_best['recall@1']} p5={text_best['precision@5']}"
    )
    print(
        f"best image={image_best_name} exact@1={image_best['exact@1']} "
        f"mean_self={image_best['mean_self_score']}"
    )
    print(json.dumps(payload["timingMs"], ensure_ascii=False))


if __name__ == "__main__":
    main()
