# -*- coding: utf-8 -*-
"""
Адаптация YML-фида Тильды под Яндекс Директ и Яндекс Товары (Вебмастер).

Вход:  фид Тильды по ссылке FEED_URL (или локальная копия через --from-file)
       список товаров и разделов каталога через открытое API Тильды PARTS_URL
       (или локальная копия JSON через --parts-file)
Выход: Яндекс Директ/feeds/elektromontazhnik_direct.yml  — для Директа (ЕПК)
       Яндекс Директ/feeds/elektromontazhnik_tovary.xml  — для Яндекс Товаров
       (то, что раньше было «Товары и цены» в Вебмастере; merchants.yandex.ru)
       Яндекс Директ/feeds/elektromontazhnik_business.xml — для Яндекс Бизнеса
       (прайс-лист профиля компании и рекламная подписка)

Общее для обоих фидов:
  1. В <categories> добавляются подразделы Тильды с parentId на родительский раздел.
  2. У каждого <offer> <categoryId> меняется на подраздел.
Только для Директа (target=direct):
  3. У офферов добавляются два <collectionId> (подраздел и родитель), после </offers>
     идет <collections> — страницы каталога для ЕПК.
Только для Яндекс Товаров (target=tovary), по справке merchants/ru/features и offers:
  3. Убирается <manufacturer_warranty> (Товары его не рекомендуют), у офферов ставится
     available="true", в date подставляется время сборки (файл не старше 10 дней). Расширение .xml, чтобы GitHub Pages отдавал text/xml — text/yaml Товары не принимают.
Все остальное в фиде (шапка, офферы, param) не трогается — обработка текстовая,
чтобы не ломать CDATA и форматирование Тильды.
Для Яндекс Бизнеса (target=business) файл собирается заново по справке
business-priority/ru/manage/price-list (раздел «Загрузка файла YML»): плоские категории
(подразделы без parentId), у оффера только name, vendor, price, currencyId=RUB, categoryId,
одна picture, description без HTML, shortDescription до 250 знаков, url.

Запуск: PYTHONIOENCODING=utf-8 python scripts/direct_feed.py [--target direct|tovary|business|all]
        [--check-urls] [--publish] [--from-file feed.yml] [--parts-file parts.json] [--out путь.yml]

--publish копирует результат и сам скрипт в репозиторий feed-github и делает git commit + push;
фиды публикуются на GitHub Pages: https://izgus.github.io/feed/<имя файла>.
В репозитории GitHub Actions (.github/workflows/build.yml) раз в сутки запускает
`python direct_feed.py --out-dir .` и коммитит результат, так что фиды обновляются сами.
"""
import argparse
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, 'Яндекс Директ', 'feeds')
OUT_FILES = OrderedDict([
    ('direct', os.path.join(DIR, 'elektromontazhnik_direct.yml')),
    ('tovary', os.path.join(DIR, 'elektromontazhnik_tovary.xml')),
    ('business', os.path.join(DIR, 'elektromontazhnik_business.xml')),
])
REPO_DIR = os.path.join(ROOT, 'feed-github')   # локальный клон github.com/IzGus/feed
PUBLIC_BASE = 'https://izgus.github.io/feed/'

FEED_URL = 'https://elektromontazhnik64.ru/tstore/yml/1978c3209b2f0415073ecc572f15d09d.yml'
STOREPART = '932478714073'   # uid каталога Тильды
RECID = '3293272201'         # recid блока каталога на странице /generatory-i-ibp/
PARTS_URL = ('https://store.tildaapi.com/api/getproductslist/?storepartuid=%s'
             '&recid=%s&getparts=true&size=500&slice=1' % (STOREPART, RECID))

BASE = 'https://elektromontazhnik64.ru/generatory-i-ibp/'
PARENT_UID = '286678031193'  # «Генераторы и резервное питание»
# Картинка раздела в Тильде (800x800 png)
PARENT_IMG = 'https://static.tildacdn.com/stor6232-3334-4732-a231-623862646362/8728266aa6fb7b8d9a4c234813d0dbe0.png'

MAX_PICTURES = 5  # картинок на каталог

# Тексты каталогов (страниц) для Директа. Ключ — uid раздела Тильды.
# Без буквы «ё». Ссылки подразделов проверены 09.09.2026: блок «Каталог» (t1291)
# понимает только tfc_storepartnav с числовым uid подраздела.
COLLECTIONS = OrderedDict([
    (PARENT_UID, {
        'url': BASE,
        'name': 'Генераторы и ИБП в Балашове',
        'description': ('Генераторы и источники бесперебойного питания для дома, '
                        'объектов и производства. Подбор по параметрам. '
                        'Самовывоз в Балашове и доставка по Саратовской области.'),
    }),
    ('780518169663', {
        'name': 'Инверторные бензогенераторы в Балашове',
        'description': ('Инверторные бензиновые генераторы ТСС от 1,2 до 6 кВт '
                        'с чистой синусоидой для чувствительной электроники. '
                        'Самовывоз в Балашове, доставка по Саратовской области.'),
    }),
    ('141015326003', {
        'name': 'Бензиновые генераторы в Балашове',
        'description': ('Бензиновые генераторы ТСС от 2 до 8 кВт, однофазные и '
                        'трехфазные, с ручным и электрическим запуском. '
                        'Самовывоз в Балашове, доставка по Саратовской области.'),
    }),
    ('594805333963', {
        'name': 'Газовые и гибридные генераторы в Балашове',
        'description': ('Газовые генераторы Steinmets от 7 до 18 кВт и гибридные '
                        'газ-бензин генераторы ТСС для дома и объектов. '
                        'Самовывоз в Балашове, доставка по Саратовской области.'),
    }),
    ('259723601923', {
        'name': 'Источники бесперебойного питания (ИБП) в Балашове',
        'description': ('ИБП Hiden Control для газовых котлов, насосов и домашней '
                        'техники с внешними аккумуляторами. '
                        'Самовывоз в Балашове, доставка по Саратовской области.'),
    }),
])


def subpart_url(uid):
    return BASE + '?tfc_storepartnav%%5B%s%%5D=%s&tfc_div=:::' % (RECID, uid)


def xml_escape(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def strip_html(text):
    """HTML из описания Тильды -> простой текст: теги в пробелы, сущности раскрыты."""
    import html as _html
    t = re.sub(r'<br\s*/?>|</p>|</div>|</li>', '\n', text or '', flags=re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = _html.unescape(t)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\s*\n\s*', '\n', t)
    return t.strip()


def short_text(text, limit=250):
    """Первые предложения текста, не длиннее limit знаков, без обрыва слова."""
    t = re.sub(r'\s+', ' ', text).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    # сначала граница предложения (даже если получится коротко), потом запятая, потом слово
    for sep, minpos in (('. ', limit // 4), ('! ', limit // 4), ('; ', limit // 2), (', ', limit // 2), (' ', limit // 2)):
        i = cut.rfind(sep)
        if i > minpos:
            return cut[:i + (1 if sep[0] in '.!' else 0)].rstrip(' ,;')
    return cut.rstrip()


def fetch(url, binary=False, tries=3):
    # Тильда и ее API изредка отвечают 403 на первый запрос — повторяем с паузой.
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            return data if binary else data.decode('utf-8')
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(5 * (i + 1))
    raise SystemExit('Не удалось скачать %s: %s' % (url, last))


def head_ok(url):
    req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status == 200
    except Exception as e:  # noqa
        return False


def load_inputs(args):
    if args.from_file:
        feed = io.open(args.from_file, encoding='utf-8').read()
    else:
        feed = fetch(FEED_URL)
    if args.parts_file:
        parts = json.load(io.open(args.parts_file, encoding='utf-8'))
    else:
        parts = json.loads(fetch(PARTS_URL))
    return feed, parts


def build(feed, parts, target='direct'):
    warnings = []
    direct = target == 'direct'

    # --- разделы Тильды ---------------------------------------------------
    root_part = None
    for p in parts.get('parts', []):
        if str(p['uid']) == PARENT_UID:
            root_part = p
    if root_part is None:
        raise SystemExit('В ответе API Тильды нет раздела %s' % PARENT_UID)
    subparts = sorted(root_part.get('subparts', []), key=lambda s: s.get('sort', 0))
    sub_by_uid = OrderedDict((str(s['uid']), s['title']) for s in subparts)
    for uid in COLLECTIONS:
        if uid != PARENT_UID and uid not in sub_by_uid:
            warnings.append('Подраздел %s из COLLECTIONS отсутствует в Тильде' % uid)
    for uid, title in sub_by_uid.items():
        if uid not in COLLECTIONS:
            warnings.append('В Тильде появился подраздел %s «%s» — нет текста в COLLECTIONS' % (uid, title))

    # --- товар -> подраздел, первые фото -----------------------------------
    prod_sub = {}
    first_photo = {}
    for pr in parts.get('products', []):
        uid = str(pr['uid'])
        try:
            partuids = [str(x) for x in json.loads(pr.get('partuids') or '[]')]
        except ValueError:
            partuids = []
        subs = [u for u in partuids if u in sub_by_uid]
        if len(subs) > 1:
            warnings.append('Товар %s «%s» сразу в нескольких подразделах: %s — берем первый'
                            % (uid, pr.get('title'), ', '.join(subs)))
        prod_sub[uid] = subs[0] if subs else None
        try:
            gallery = json.loads(pr.get('gallery') or '[]')
        except ValueError:
            gallery = []
        if gallery:
            first_photo[uid] = gallery[0]['img']

    if target == 'business':
        return build_business(feed, root_part, sub_by_uid, prod_sub, warnings)

    # --- <categories> ----------------------------------------------------
    cats = ['\t\t<categories>',
            '\t\t\t<category id="%s">%s</category>' % (PARENT_UID, xml_escape(root_part['title']))]
    for uid, title in sub_by_uid.items():
        cats.append('\t\t\t<category id="%s" parentId="%s">%s</category>' % (uid, PARENT_UID, xml_escape(title)))
    cats.append('\t\t</categories>')
    feed, n = re.subn(r'[ \t]*<categories>.*?</categories>', '\n'.join(cats), feed, count=1, flags=re.S)
    if n != 1:
        raise SystemExit('В фиде не найден блок <categories>')

    # --- офферы -----------------------------------------------------------
    per_cat = defaultdict(int)
    per_coll = defaultdict(int)
    coll_offers = defaultdict(list)  # uid каталога -> offer id (для картинок)
    seen = set()

    def fix_offer(m):
        oid, attrs, body = m.group(1), m.group(2), m.group(3)
        seen.add(oid)
        sub = prod_sub.get(oid)
        if oid not in prod_sub:
            warnings.append('Оффер %s есть в фиде, но не найден в API каталога — оставлен в родительской категории' % oid)
        elif sub is None:
            warnings.append('Оффер %s без подраздела — оставлен в родительской категории' % oid)
        cat = sub or PARENT_UID
        colls = ([sub] if sub else []) + [PARENT_UID]
        cm = re.search(r'([ \t]*)<categoryId>[^<]*</categoryId>', body)
        if not cm:
            warnings.append('Оффер %s без <categoryId>' % oid)
            return m.group(0)
        ind = cm.group(1)
        repl = '%s<categoryId>%s</categoryId>' % (ind, cat)
        if direct:
            for c in colls:
                repl += '\n%s<collectionId>%s</collectionId>' % (ind, c)
        body = body[:cm.start()] + repl + body[cm.end():]
        if not direct:
            # Яндекс Товары: manufacturer_warranty не рекомендован, available нужен для показа
            body = re.sub(r'\n[ \t]*<manufacturer_warranty>[^<]*</manufacturer_warranty>', '', body)
            if 'available=' not in attrs:
                attrs += ' available="true"'
        per_cat[cat] += 1
        for c in colls:
            per_coll[c] += 1
            coll_offers[c].append(oid)
        return '<offer id="%s"%s>%s</offer>' % (oid, attrs, body)

    # <offer id="..." ...> ... </offer>; группа 2 — прочие атрибуты (available и т.п.)
    feed = re.sub(r'<offer id="([^"]+)"([^>]*)>(.*?)</offer>', fix_offer, feed, flags=re.S)

    for uid in prod_sub:
        if uid not in seen:
            warnings.append('Товар %s есть в API каталога, но нет в фиде' % uid)

    # --- <collections> (только Директ) ------------------------------------
    lines = ['\t\t<collections>']
    for uid, c in COLLECTIONS.items():
        url = c.get('url') or subpart_url(uid)
        pics = []
        if uid == PARENT_UID:
            pics.append(PARENT_IMG)
            # по одному фото из каждого подраздела
            for s in sub_by_uid:
                for oid in coll_offers.get(s, []):
                    if oid in first_photo:
                        pics.append(first_photo[oid])
                        break
        else:
            for oid in coll_offers.get(uid, []):
                if oid in first_photo:
                    pics.append(first_photo[oid])
                if len(pics) >= MAX_PICTURES:
                    break
        pics = pics[:MAX_PICTURES]
        if not pics:
            warnings.append('Каталог %s без картинок' % uid)
        lines.append('\t\t\t<collection id="%s">' % uid)
        lines.append('\t\t\t\t<url>%s</url>' % xml_escape(url))
        for p in pics:
            lines.append('\t\t\t\t<picture>%s</picture>' % xml_escape(p))
        lines.append('\t\t\t\t<name>%s</name>' % xml_escape(c['name']))
        if c.get('description'):
            lines.append('\t\t\t\t<description>%s</description>' % xml_escape(c['description']))
        lines.append('\t\t\t</collection>')
    lines.append('\t\t</collections>')
    if direct:
        feed, n = re.subn(r'([ \t]*</offers>)', lambda m: m.group(1) + '\n' + '\n'.join(lines), feed, count=1)
        if n != 1:
            raise SystemExit('В фиде не найден </offers>')
    else:
        # дата сборки в формате RFC 3339 с поясом (Товары требуют файл не старше 10 дней)
        tz = datetime.timezone(datetime.timedelta(hours=3))
        now = datetime.datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S+03:00')
        feed, n = re.subn(r'<yml_catalog date="[^"]*">', '<yml_catalog date="%s">' % now, feed, count=1)
        if n != 1:
            warnings.append('Не найден атрибут date у <yml_catalog>')

    stats = {
        'offers': len(seen),
        'per_cat': per_cat,
        'per_coll': per_coll,
        'cat_titles': dict(sub_by_uid, **{PARENT_UID: root_part['title']}),
    }
    return feed, stats, warnings


def build_business(feed, root_part, sub_by_uid, prod_sub, warnings):
    """Фид для Яндекс Бизнеса: собирается заново из разобранного фида Тильды."""
    src = ET.fromstring(feed.encode('utf-8'))
    shop = src.find('shop')
    per_cat = defaultdict(int)
    used_cats = OrderedDict()
    offers_xml = []
    seen = set()
    for o in shop.findall('offers/offer'):
        oid = o.get('id')
        seen.add(oid)
        sub = prod_sub.get(oid)
        if sub is None:
            warnings.append('Оффер %s без подраздела — отнесен к «%s»' % (oid, root_part['title']))
        cat = sub or PARENT_UID
        used_cats[cat] = sub_by_uid.get(cat, root_part['title'])
        per_cat[cat] += 1
        name = (o.findtext('name') or '').strip()
        vendor = (o.findtext('vendor') or '').strip()
        if not vendor:
            warnings.append('Оффер %s без vendor — для Бизнеса он обязателен' % oid)
        price = (o.findtext('price') or '').strip()
        if price.endswith('.00'):
            price = price[:-3]
        pics = [p.text.strip() for p in o.findall('picture') if p.text]
        descr = strip_html(o.findtext('description') or '')
        if re.search(r'https?://|www\.', descr) or re.search(r'\+?\d[\d\s()-]{9,}\d', descr):
            warnings.append('Оффер %s: в описании ссылка или телефон — Бизнес такое не публикует' % oid)
        descr = descr[:3000]
        short = short_text(descr, 250)
        lines = ['\t\t<offer id="%s">' % oid,
                 '\t\t\t<name>%s</name>' % xml_escape(name[:250]),
                 '\t\t\t<vendor>%s</vendor>' % xml_escape(vendor),
                 '\t\t\t<price>%s</price>' % price,
                 '\t\t\t<currencyId>RUB</currencyId>',
                 '\t\t\t<categoryId>%s</categoryId>' % cat]
        if pics:
            lines.append('\t\t\t<picture>%s</picture>' % xml_escape(pics[0]))
        else:
            warnings.append('Оффер %s без картинки' % oid)
        if descr:
            lines.append('\t\t\t<description>%s</description>' % xml_escape(descr))
            lines.append('\t\t\t<shortDescription>%s</shortDescription>' % xml_escape(short))
        url = (o.findtext('url') or '').strip()
        if url:
            lines.append('\t\t\t<url>%s</url>' % xml_escape(url[:512]))
        lines.append('\t\t</offer>')
        offers_xml.append('\n'.join(lines))
    for uid in prod_sub:
        if uid not in seen:
            warnings.append('Товар %s есть в API каталога, но нет в фиде' % uid)

    # категории — плоские, в порядке подразделов Тильды
    ordered = [(u, t) for u, t in sub_by_uid.items() if u in used_cats]
    if PARENT_UID in used_cats:
        ordered.append((PARENT_UID, root_part['title']))
    tz = datetime.timezone(datetime.timedelta(hours=3))
    now = datetime.datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S+03:00')
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<yml_catalog date="%s">' % now,
           '\t<shop>',
           '\t\t<name>%s</name>' % xml_escape(shop.findtext('name') or ''),
           '\t\t<company>%s</company>' % xml_escape(shop.findtext('company') or ''),
           '\t\t<url>%s</url>' % xml_escape(shop.findtext('url') or ''),
           '\t\t<currencies>',
           '\t\t\t<currency id="RUB" rate="1"/>',
           '\t\t</currencies>',
           '\t\t<categories>']
    for uid, title in ordered:
        out.append('\t\t\t<category id="%s">%s</category>' % (uid, xml_escape(title)))
    out += ['\t\t</categories>', '\t\t<offers>']
    out += offers_xml
    out += ['\t\t</offers>', '\t</shop>', '</yml_catalog>', '']
    stats = {
        'offers': len(seen),
        'per_cat': per_cat,
        'per_coll': {},
        'cat_titles': OrderedDict(ordered),
    }
    return '\n'.join(out), stats, warnings


def validate(text, check_urls=False, target='direct'):
    """Структурная проверка результата. Возвращает список ошибок."""
    errors = []
    try:
        root = ET.fromstring(text.encode('utf-8'))
    except ET.ParseError as e:
        return ['XML не парсится: %s' % e]
    shop = root.find('shop')
    cat_ids = {c.get('id') for c in shop.findall('categories/category')}
    for c in shop.findall('categories/category'):
        pid = c.get('parentId')
        if pid and pid not in cat_ids:
            errors.append('parentId %s категории %s не существует' % (pid, c.get('id')))
    colls = shop.findall('collections/collection')
    coll_ids = {c.get('id') for c in colls}
    urls = []
    for c in colls:
        cid = c.get('id')
        if c.find('url') is None or not (c.findtext('url') or '').strip():
            errors.append('Каталог %s без url' % cid)
        if not (c.findtext('name') or '').strip():
            errors.append('Каталог %s без name' % cid)
        pics = [p.text for p in c.findall('picture')]
        if not pics:
            errors.append('Каталог %s без picture' % cid)
        urls.append(c.findtext('url'))
        urls.extend(pics)
    if target == 'business':
        # справка Яндекс Бизнеса: плоские категории, RUB, одна картинка, обязательный vendor
        for c in shop.findall('categories/category'):
            if c.get('parentId'):
                errors.append('Категория %s с parentId — для Бизнеса категории плоские' % c.get('id'))
        for o in shop.findall('offers/offer'):
            oid = o.get('id')
            for tag in ('name', 'vendor', 'price', 'currencyId', 'categoryId'):
                if not (o.findtext(tag) or '').strip():
                    errors.append('Оффер %s без %s' % (oid, tag))
            if o.findtext('currencyId') != 'RUB':
                errors.append('Оффер %s: валюта должна быть RUB' % oid)
            if o.findtext('categoryId') not in cat_ids:
                errors.append('Оффер %s: categoryId нет в <categories>' % oid)
            if len(o.findall('picture')) != 1:
                errors.append('Оффер %s: должна быть ровно одна picture' % oid)
            if len(oid) > 80 or re.search(r'[^0-9A-Za-zА-Яа-я.,/\\()\[\]=-]', oid):
                errors.append('Оффер %s: недопустимый id' % oid)
            if len(o.findtext('name') or '') > 250:
                errors.append('Оффер %s: название длиннее 250' % oid)
            if len(o.findtext('description') or '') > 3000:
                errors.append('Оффер %s: описание длиннее 3000' % oid)
            if len(o.findtext('shortDescription') or '') > 250:
                errors.append('Оффер %s: shortDescription длиннее 250' % oid)
            if len(o.findtext('url') or '') > 512:
                errors.append('Оффер %s: url длиннее 512' % oid)
            if not re.fullmatch(r'[0-9]+([.,][0-9]+)?', o.findtext('price') or '') or float((o.findtext('price') or '0').replace(',', '.')) <= 0:
                errors.append('Оффер %s: цена должна быть положительным числом' % oid)
            if check_urls:
                urls.append(o.findtext('picture'))
        if check_urls:
            for u in urls:
                if not head_ok(u):
                    errors.append('URL не отвечает 200: %s' % u)
        return errors
    if target == 'direct' and not colls:
        errors.append('Нет блока <collections>')
    if target == 'tovary' and shop.find('collections') is not None:
        errors.append('В фиде для Товаров не должно быть <collections>')
    for o in shop.findall('offers/offer'):
        oid = o.get('id')
        cid = o.findtext('categoryId')
        if cid not in cat_ids:
            errors.append('Оффер %s: categoryId %s нет в <categories>' % (oid, cid))
        cls = [x.text for x in o.findall('collectionId')]
        if target == 'direct' and not cls:
            errors.append('Оффер %s без collectionId' % oid)
        if target == 'tovary' and cls:
            errors.append('Оффер %s: collectionId лишний для Товаров' % oid)
        for x in cls:
            if x not in coll_ids:
                errors.append('Оффер %s: collectionId %s нет в <collections>' % (oid, x))
        for tag in ('name', 'url', 'price', 'currencyId', 'picture', 'description'):
            if not (o.findtext(tag) or '').strip():
                errors.append('Оффер %s без %s' % (oid, tag))
        if target == 'tovary':
            # требования Яндекс Товаров (справка merchants/ru: offers, attributes-offer, description)
            if o.get('available') != 'true':
                errors.append('Оффер %s без available="true"' % oid)
            if o.find('manufacturer_warranty') is not None:
                errors.append('Оффер %s: manufacturer_warranty не рекомендован для Товаров' % oid)
            if len(oid) > 20 or re.search(r'[^0-9A-Za-zА-Яа-я.,/\\()\[\]=-]', oid):
                errors.append('Оффер %s: недопустимый id для Товаров' % oid)
            plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', o.findtext('description') or '')).strip()
            if len(plain) < 70:
                errors.append('Оффер %s: описание короче 70 знаков (%d)' % (oid, len(plain)))
            if len(plain) > 3000:
                errors.append('Оффер %s: описание длиннее 3000 знаков (%d)' % (oid, len(plain)))
            if len(o.findtext('name') or '') > 150:
                errors.append('Оффер %s: название длиннее 150 знаков' % oid)
    if check_urls:
        for u in urls:
            if not head_ok(u):
                errors.append('URL не отвечает 200: %s' % u)
    return errors


def publish(out_paths):
    """Копирует фиды в репозиторий feed-github и отправляет на GitHub."""
    for out_path in out_paths:
        shutil.copyfile(out_path, os.path.join(REPO_DIR, os.path.basename(out_path)))
    # копия скрипта в репозитории — ее запускает GitHub Actions раз в сутки
    shutil.copyfile(os.path.abspath(__file__), os.path.join(REPO_DIR, 'direct_feed.py'))
    def git(*a):
        return subprocess.run(['git', '-C', REPO_DIR] + list(a), capture_output=True, text=True)
    st = git('status', '--porcelain')
    if not st.stdout.strip():
        print('Публикация: изменений нет, на GitHub уже актуальные файлы')
        return
    git('add', '-A')
    r = git('commit', '-m', 'Обновление фидов')
    if r.returncode:
        print('git commit не удался:\n' + r.stdout + r.stderr)
        sys.exit(2)
    r = git('push')
    if r.returncode:
        print('git push не удался:\n' + r.stdout + r.stderr)
        sys.exit(2)
    for out_path in out_paths:
        print('Опубликовано: %s%s' % (PUBLIC_BASE, os.path.basename(out_path)))
    print('GitHub Pages обновится в течение минуты-двух')


def main():
    ap = argparse.ArgumentParser(description='Адаптация фида Тильды под Яндекс Директ и Яндекс Товары')
    ap.add_argument('--target', choices=['direct', 'tovary', 'business', 'all'], default='all',
                    help='какой фид собирать (по умолчанию все)')
    ap.add_argument('--from-file', help='локальная копия фида Тильды вместо скачивания')
    ap.add_argument('--parts-file', help='локальная копия ответа API Тильды (JSON)')
    ap.add_argument('--out', help='куда писать результат (.yml или .xml); только при одном --target')
    ap.add_argument('--out-dir', help='папка для результатов вместо «Яндекс Директ/feeds» (используется в GitHub Actions)')
    ap.add_argument('--check-urls', action='store_true', help='проверить HEAD-запросом url и картинки каталогов')
    ap.add_argument('--publish', action='store_true', help='скопировать в feed-github и сделать git commit + push')
    args = ap.parse_args()

    targets = list(OUT_FILES) if args.target == 'all' else [args.target]
    if args.out and len(targets) > 1:
        ap.error('--out работает только с одним --target')

    if args.out_dir:
        for t in OUT_FILES:
            OUT_FILES[t] = os.path.join(args.out_dir, os.path.basename(OUT_FILES[t]))

    feed, parts = load_inputs(args)
    written = []
    failed = False
    for target in targets:
        out = args.out or OUT_FILES[target]
        result, stats, warnings = build(feed, parts, target=target)
        errors = validate(result, check_urls=args.check_urls, target=target)

        os.makedirs(os.path.dirname(out), exist_ok=True)
        with io.open(out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(result)

        print('=== %s: офферов %d' % (target, stats['offers']))
        print('По категориям:')
        for uid, title in stats['cat_titles'].items():
            print('  %s  %-40s %d' % (uid, title, stats['per_cat'].get(uid, 0)))
        if target == 'direct':
            print('Каталоги (collections):')
            for uid, c in COLLECTIONS.items():
                print('  %s  %-52s %d товаров' % (uid, c['name'], stats['per_coll'].get(uid, 0)))
        for w in warnings:
            print('ПРЕДУПРЕЖДЕНИЕ: ' + w)
        for e in errors:
            print('ОШИБКА: ' + e)
        print('Записано: %s (%d байт)' % (out, len(result.encode('utf-8'))))
        if errors:
            failed = True
        else:
            written.append(out)

    if failed:
        sys.exit(1)
    if args.publish:
        publish(written)


if __name__ == '__main__':
    main()
