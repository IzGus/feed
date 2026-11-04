# -*- coding: utf-8 -*-
"""
Фиды каталога elektromontazhnik64.ru для Яндекс Директа, Яндекс Товаров и Яндекс Бизнеса.

Источник — открытое API каталога Тильды (один запрос отдает все товары и дерево разделов):
  https://store.tildaapi.com/api/getproductslist/?storepartuid=932478714073&recid=3293272201&getparts=true
До 04.11.2026 скрипт правил YML-выгрузку Тильды, но она отдает только раздел «Генераторы»
(настройку выгрузки через MCP прочитать не удалось), а поля YML один в один есть в API:
name = title, description = короткий текст, picture = gallery, param = характеристики, url, vendorCode = sku.
id офферов прежние (uid товара Тильды), так что статистика в Директе не теряется.

Выход (по умолчанию «Яндекс Директ/feeds», в GitHub Actions — --out-dir .):
  elektromontazhnik_direct.yml   — Директ (ЕПК): дерево категорий, у оффера два <collectionId>
                                   (подраздел и раздел), после </offers> — <collections>: страницы разделов
                                   и подразделов каталога (справка direct/feeds/requirements-yml, «Каталоги»);
  elektromontazhnik_tovary.xml   — Яндекс Товары (merchants.yandex.ru): без collections и manufacturer_warranty,
                                   available="true", date = время сборки (файл не старше 10 дней), .xml — чтобы
                                   GitHub Pages отдавал text/xml;
  elektromontazhnik_business.xml — Яндекс Бизнес (business-priority/ru/manage/price-list, «Загрузка файла YML»):
                                   плоские категории, RUB, одна picture, vendor обязателен, description без HTML
                                   до 3000, shortDescription до 250.

Категория оффера — подраздел, а если его нет (Мотопомпы, Снегоуборщики) — раздел.
Тексты каталогов Директа — SECTIONS и SUBS ниже (без «ё», без брендов: страницы разделов — направления,
бренды там не называем; диапазоны сверены с характеристиками товаров 04.11.2026).
Новый раздел или подраздел в Тильде без текста попадает в фид с названием «<раздел> в Балашове»
и предупреждением — текст нужно дописать сюда.

DIRECT_ROOTS — какие разделы идут в фид Директа. Решение владельца: в фид Директа идут все товары, нужное
выбирается фильтрами кампаний. До 04.11.2026 в фиде Директа были только генераторы; владелец поставил фильтр
по категории у кампании на генераторы — с 04.11 все разделы (None). Новая кампания без фильтра покажет весь каталог.

Запуск: PYTHONIOENCODING=utf-8 python scripts/direct_feed.py [--target direct|tovary|business|all]
        [--check-urls] [--publish] [--api-file catalog.json] [--out путь] [--out-dir папка]

--publish копирует фиды и сам скрипт в репозиторий feed-github и делает git commit + push;
фиды публикуются на GitHub Pages: https://izgus.github.io/feed/<имя файла>.
В репозитории GitHub Actions (.github/workflows/build.yml) раз в сутки запускает
`python direct_feed.py --out-dir .` и коммитит результат, так что фиды обновляются сами.
"""
import argparse
import datetime
import html
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

SITE = 'https://elektromontazhnik64.ru'
SHOP_NAME = 'Электромонтажник'
COMPANY = 'Электромонтажник'
CATALOG_UID = '932478714073'   # корень каталога Тильды (проект 443554)
API_RECID = '3293272201'       # любой блок каталога; с корнем отдает все товары
API_URL = ('https://store.tildaapi.com/api/getproductslist/?storepartuid=%s&recid=%s'
           '&getparts=true&size=500&slice=%%d' % (CATALOG_UID, API_RECID))

MAX_PICTURES = 5   # картинок на каталог Директа
GEN = '286678031193'
DIRECT_ROOTS = None   # None — все разделы; (GEN,) — только генераторы

TAIL = 'Самовывоз в Балашове, доставка по Саратовской области.'

# Разделы: страница с блоком каталога (recid — для ссылок на подразделы) и текст каталога Директа.
SECTIONS = OrderedDict([
    (GEN, {
        'page': '/generatory-i-ibp/', 'recid': '3293272201',
        'name': 'Генераторы и ИБП в Балашове',
        'description': 'Бензиновые, инверторные, газовые и дизельные генераторы от 1 до 17 кВт, '
                       'ИБП для котлов и насосов. Подбор по мощности. ' + TAIL,
        # картинка раздела в Тильде (800×800 png)
        'picture': 'https://static.tildacdn.com/stor6232-3334-4732-a231-623862646362/8728266aa6fb7b8d9a4c234813d0dbe0.png',
    }),
    ('178199766063', {
        'page': '/motopompy/', 'recid': '5413909801',
        'name': 'Мотопомпы в Балашове',
        'description': 'Мотопомпы от 7 до 140 м³/ч для чистой и грязной воды, химических жидкостей '
                       'и пожаротушения. ' + TAIL,
    }),
    ('992980329813', {
        'page': '/sadovaya-tehnika/', 'recid': '5414049001',
        'name': 'Садовая и уборочная техника в Балашове',
        'description': 'Газонокосилки, мотокосы, пилы, райдеры, кусторезы, воздуходувки, мойки, '
                       'пылесосы и подметальные машины. ' + TAIL,
    }),
    ('447434842163', {
        'page': '/snegouborshchiki/', 'recid': '5414050801',
        'name': 'Снегоуборщики в Балашове',
        'description': 'Бензиновые и электрические снегоуборщики с шириной захвата от 33 до 81 см, '
                       'на колесах и гусеницах. ' + TAIL,
    }),
    ('153453344023', {
        'page': '/rashodniki-i-aksessuary/', 'recid': '5414051801',
        'name': 'Расходники и аксессуары в Балашове',
        'description': 'Пильные цепи и шины, леска и триммерные головки, ножи для мотокос, масла, '
                       'аккумуляторы и аксессуары. ' + TAIL,
    }),
])

SUBS = {
    # Генераторы и резервное питание
    '780518169663': ('Инверторные бензогенераторы в Балашове',
                     'Инверторные бензогенераторы от 1 до 9,5 кВт с чистой синусоидой '
                     'для чувствительной электроники. '),
    '141015326003': ('Бензиновые генераторы в Балашове',
                     'Бензиновые генераторы от 1,3 до 17 кВт, однофазные и трехфазные, '
                     'с ручным и электрическим запуском. '),
    '594805333963': ('Газовые и гибридные генераторы в Балашове',
                     'Газовые генераторы на природном и сжиженном газе и гибридные газ-бензин '
                     'от 5 до 17 кВт для дома и объектов. '),
    '589327649303': ('Дизельные генераторы в Балашове',
                     'Трехфазные дизельные генераторы от 6,8 до 8 кВт для дома и объектов. '),
    '259723601923': ('Источники бесперебойного питания (ИБП) в Балашове',
                     'ИБП от 300 до 1000 Вт для газовых котлов, насосов и домашней техники '
                     'с внешними аккумуляторами. '),
    # Садовая и уборочная техника
    '983439229473': ('Пилы и высоторезы в Балашове',
                     'Бензопилы и электропилы с шиной от 10 до 20 дюймов, бензиновые высоторезы '
                     'для обрезки веток. '),
    '635066744493': ('Мотокосы и триммеры в Балашове',
                     'Бензиновые мотокосы и электротриммеры с шириной кошения от 32 до 43 см. '),
    '763428092363': ('Газонокосилки в Балашове',
                     'Бензиновые и электрические газонокосилки с шириной кошения от 32 до 56 см, '
                     'самоходные и несамоходные. '),
    '240937434233': ('Райдеры и садовые тракторы в Балашове',
                     'Райдеры и садовые тракторы с шириной кошения от 61 до 122 см для больших участков. '),
    '641267503533': ('Аэраторы и скарификаторы в Балашове',
                     'Бензиновые и электрические аэраторы-скарификаторы для ухода за газоном. '),
    '783995309283': ('Кусторезы и садовые ножницы в Балашове',
                     'Бензиновые, электрические и аккумуляторные кусторезы и ножницы '
                     'для живой изгороди и кустов. '),
    '583206479823': ('Воздуходувки в Балашове',
                     'Бензиновые и электрические воздуходувки, ранцевые и с функцией пылесоса, '
                     'для уборки листьев. '),
    '317202928353': ('Опрыскиватели, измельчители и мотобуры в Балашове',
                     'Опрыскиватели, садовые измельчители, транспортеры и мотобур '
                     'для работ на участке. '),
    '274835118493': ('Уборочная техника в Балашове',
                     'Подметальные машины, пылесосы, пылеводососы и мойка высокого давления '
                     'для двора, гаража и мастерской. '),
    # Расходники и аксессуары
    '934216823253': ('Пильные цепи и шины в Балашове',
                     'Цепи и шины для бензопил и электропил от 10 до 20 дюймов. '),
    '520866779693': ('Триммерная леска и головки в Балашове',
                     'Леска и триммерные головки для мотокос и электротриммеров. '),
    '659545492733': ('Ножи и диски для мотокос в Балашове',
                     'Двух-, трех- и четырехлопастные ножи и пильные диски для мотокос. '),
    '477046503943': ('Масла для садовой техники в Балашове',
                     'Масла для 2-тактных и 4-тактных двигателей и для смазки пильных цепей. '),
    '531891449933': ('Аксессуары для генераторов в Балашове',
                     'Аккумуляторы для электростартера, модуль параллельного подключения '
                     'инверторных генераторов, подогреватель масляного фильтра. '),
    '348797031923': ('Аксессуары для садовой техники в Балашове',
                     'Шнеки для мотобура, травосборник для аэратора, комплект для сбора листьев. '),
}


def xml_escape(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def clean(s):
    """Значение из API Тильды -> простой текст: сущности раскрыты, пробелы схлопнуты."""
    return re.sub(r'\s+', ' ', html.unescape(str(s or '')).replace('\xa0', ' ')).strip()


def strip_html(text):
    t = re.sub(r'<br\s*/?>|</p>|</div>|</li>', '\n', text or '', flags=re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = html.unescape(t)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\s*\n\s*', '\n', t)
    return t.strip()


def short_text(text, limit=250):
    """Первые предложения текста, не длиннее limit знаков, без обрыва слова."""
    t = re.sub(r'\s+', ' ', text).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep, minpos in (('. ', limit // 4), ('! ', limit // 4), ('; ', limit // 2), (', ', limit // 2), (' ', limit // 2)):
        i = cut.rfind(sep)
        if i > minpos:
            return cut[:i + (1 if sep[0] in '.!' else 0)].rstrip(' ,;')
    return cut.rstrip()


def money(v):
    return '%.2f' % float(v)


def fetch(url, tries=3):
    # Тильда и ее API изредка отвечают 403 / таймаутом — повторяем с паузой.
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode('utf-8')
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(5 * (i + 1))
    raise SystemExit('Не удалось скачать %s: %s' % (url, last))


def head_ok(url):
    req = urllib.request.Request(url, method='HEAD', headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/130.0 Safari/537.36'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status == 200
    except Exception:  # noqa
        return False


def load_catalog(api_file=None):
    if api_file:
        return json.load(io.open(api_file, encoding='utf-8'))
    data, sl = None, 1
    while True:
        d = json.loads(fetch(API_URL % sl))
        if data is None:
            data = d
        else:
            data['products'] += d.get('products') or []
        if not d.get('products') or len(data['products']) >= int(d.get('total') or 0) or not d.get('nextslice'):
            break
        sl += 1
        time.sleep(1)
    if len(data['products']) != int(data.get('total') or 0):
        raise SystemExit('API отдало %d товаров из %s' % (len(data['products']), data.get('total')))
    return data


def subpart_url(root, uid):
    s = SECTIONS[root]
    return SITE + s['page'] + '?tfc_storepartnav%%5B%s%%5D=%s&tfc_div=:::' % (s['recid'], uid)


def model(data):
    """Дерево разделов и офферы в общем виде. Возвращает (roots, subs, offers, warnings)."""
    warnings = []
    roots, subs = OrderedDict(), OrderedDict()   # uid -> title; sub uid -> (root uid, title)
    for p in sorted(data.get('parts') or [], key=lambda p: int(p.get('sort') or 0)):
        ru = str(p['uid'])
        roots[ru] = clean(p['title'])
        for s in sorted(p.get('subparts') or [], key=lambda s: int(s.get('sort') or 0)):
            subs[str(s['uid'])] = (ru, clean(s['title']))
    if not roots:
        raise SystemExit('В ответе API нет разделов (getparts)')
    for ru, t in roots.items():
        if ru not in SECTIONS:
            warnings.append('Раздел %s «%s» есть в Тильде, но нет в SECTIONS — в фиды не попадает' % (ru, t))
    for su, (ru, t) in subs.items():
        if su not in SUBS and ru in SECTIONS:
            warnings.append('Подраздел %s «%s» без текста в SUBS — в Директе название по умолчанию' % (su, t))
    for u in list(SECTIONS) + list(SUBS):
        if u not in roots and u not in subs:
            warnings.append('Раздел %s из настроек скрипта отсутствует в Тильде' % u)

    offers = []
    for pr in data['products']:
        oid = str(pr['uid'])
        pu = pr.get('partuids') or []
        pu = [str(x).strip() for x in (pu.strip('[]').split(',') if isinstance(pu, str) else pu) if str(x).strip()]
        my_subs = [u for u in pu if u in subs]
        my_roots = [u for u in pu if u in roots] or [subs[u][0] for u in my_subs]
        if len(my_subs) > 1:
            warnings.append('Товар %s «%s» в нескольких подразделах — берем первый' % (oid, clean(pr['title'])))
        sub = my_subs[0] if my_subs else None
        root = subs[sub][0] if sub else (my_roots[0] if my_roots else None)
        if root is None:
            warnings.append('Товар %s «%s» без раздела — не попадает в фиды' % (oid, clean(pr['title'])))
            continue
        if root not in SECTIONS:
            continue
        if sub and root not in my_roots:
            warnings.append('Товар %s только в подразделе, без раздела — на странице раздела его не видно' % oid)
        gallery = json.loads(pr.get('gallery') or '[]')
        price = float(pr.get('price') or 0)
        old = float(pr.get('priceold') or 0)
        x, y, z = (int(float(pr.get(k) or 0)) for k in ('pack_x', 'pack_y', 'pack_z'))
        offers.append({
            'id': oid, 'root': root, 'sub': sub, 'cat': sub or root,
            'name': clean(pr['title']), 'vendor': clean(pr.get('brand')), 'sku': clean(pr.get('sku')),
            'description': clean(pr.get('text')),
            'pictures': [g['img'] for g in gallery if g.get('img')],
            'url': pr.get('url') or '', 'price': price, 'oldprice': old if old > price else 0,
            # габариты упаковки в Тильде — мм (pack_label lwh), в YML — см. Вес не выводим: у старых
            # товаров в Тильде он записан в кг, а поле — в граммах (в YML Тильды было 0.125 кг у генератора).
            'dimensions': '%g/%g/%g' % (x / 10, y / 10, z / 10) if x and y and z else '',
            'params': [(clean(c['title']), clean(c['value'])) for c in pr.get('characteristics') or []],
        })
    return roots, subs, offers, warnings


def collections_of(roots, subs, offers):
    """Каталоги Директа: раздел и его подразделы, в которых есть офферы."""
    by = defaultdict(list)
    for o in offers:
        by[o['root']].append(o)
        if o['sub']:
            by[o['sub']].append(o)
    out = OrderedDict()
    for ru in roots:
        if ru not in SECTIONS or not by.get(ru):
            continue
        s = SECTIONS[ru]
        pics = [s['picture']] if s.get('picture') else []
        my_subs = [su for su, (r, _) in subs.items() if r == ru and by.get(su)]
        for su in my_subs:   # по фото из каждого подраздела
            pics.append(by[su][0]['pictures'][0])
        for o in by[ru]:
            if len(pics) >= MAX_PICTURES:
                break
            if o['pictures'] and o['pictures'][0] not in pics:
                pics.append(o['pictures'][0])
        out[ru] = {'url': SITE + s['page'], 'name': s['name'], 'description': s['description'],
                   'pictures': pics[:MAX_PICTURES]}
        for su in my_subs:
            name, descr = SUBS.get(su, ('%s в Балашове' % subs[su][1], ''))
            out[su] = {'url': subpart_url(ru, su), 'name': name,
                       'description': (descr + TAIL) if descr else '',
                       'pictures': [o['pictures'][0] for o in by[su] if o['pictures']][:MAX_PICTURES]}
    return out


def now_str():
    tz = datetime.timezone(datetime.timedelta(hours=3))
    return datetime.datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S+03:00')


def build(data, target):
    roots, subs, offers, warnings = model(data)
    if target == 'direct' and DIRECT_ROOTS is not None:
        offers = [o for o in offers if o['root'] in DIRECT_ROOTS]
    used_roots = [r for r in roots if any(o['root'] == r for o in offers)]
    used_subs = [s for s in subs if any(o['sub'] == s for o in offers)]
    biz = target == 'business'
    cur = 'RUB' if biz else 'RUR'

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<yml_catalog date="%s">' % now_str(),
           '\t<shop>',
           '\t\t<name>%s</name>' % xml_escape(SHOP_NAME),
           '\t\t<company>%s</company>' % xml_escape(COMPANY),
           '\t\t<url>%s</url>' % SITE,
           '\t\t<currencies>',
           '\t\t\t<currency id="%s" rate="1"/>' % cur,
           '\t\t</currencies>',
           '\t\t<categories>']
    for ru in used_roots:
        if not biz or ru in [o['cat'] for o in offers]:
            out.append('\t\t\t<category id="%s">%s</category>' % (ru, xml_escape(roots[ru])))
        for su in used_subs:
            if subs[su][0] == ru:
                par = '' if biz else ' parentId="%s"' % ru
                out.append('\t\t\t<category id="%s"%s>%s</category>' % (su, par, xml_escape(subs[su][1])))
    out += ['\t\t</categories>', '\t\t<offers>']

    for o in offers:
        if biz:
            if not o['vendor']:
                warnings.append('Оффер %s без vendor — для Бизнеса он обязателен' % o['id'])
            descr = strip_html(o['description'])[:3000]
            if re.search(r'https?://|www\.', descr) or re.search(r'\+?\d[\d\s()-]{9,}\d', descr):
                warnings.append('Оффер %s: в описании ссылка или телефон — Бизнес такое не публикует' % o['id'])
            lines = ['\t\t<offer id="%s">' % o['id'],
                     '\t\t\t<name>%s</name>' % xml_escape(o['name'][:250]),
                     '\t\t\t<vendor>%s</vendor>' % xml_escape(o['vendor']),
                     '\t\t\t<price>%s</price>' % ('%g' % o['price'] if o['price'] == int(o['price']) else money(o['price'])),
                     '\t\t\t<currencyId>RUB</currencyId>',
                     '\t\t\t<categoryId>%s</categoryId>' % o['cat']]
            if o['pictures']:
                lines.append('\t\t\t<picture>%s</picture>' % xml_escape(o['pictures'][0]))
            if descr:
                lines.append('\t\t\t<description>%s</description>' % xml_escape(descr))
                lines.append('\t\t\t<shortDescription>%s</shortDescription>' % xml_escape(short_text(descr)))
            lines.append('\t\t\t<url>%s</url>' % xml_escape(o['url'][:512]))
            lines.append('\t\t</offer>')
            out += lines
            continue
        attrs = ' available="true"' if target == 'tovary' else ''
        lines = ['\t\t<offer id="%s"%s>' % (o['id'], attrs),
                 '\t\t\t<name>%s</name>' % xml_escape(o['name']),
                 '\t\t\t<vendor>%s</vendor>' % xml_escape(o['vendor'])]
        if o['sku']:
            lines.append('\t\t\t<vendorCode>%s</vendorCode>' % xml_escape(o['sku']))
        lines.append('\t\t\t<description>%s</description>' % xml_escape(o['description']))
        lines += ['\t\t\t<picture>%s</picture>' % xml_escape(p) for p in o['pictures']]
        lines += ['\t\t\t<url>%s</url>' % xml_escape(o['url']),
                  '\t\t\t<price>%s</price>' % money(o['price'])]
        if o['oldprice']:
            lines.append('\t\t\t<oldprice>%s</oldprice>' % money(o['oldprice']))
        lines += ['\t\t\t<currencyId>RUR</currencyId>',
                  '\t\t\t<categoryId>%s</categoryId>' % o['cat']]
        if target == 'direct':
            lines += ['\t\t\t<collectionId>%s</collectionId>' % c for c in ([o['sub']] if o['sub'] else []) + [o['root']]]
            lines.append('\t\t\t<manufacturer_warranty>true</manufacturer_warranty>')
        lines += ['\t\t\t<delivery>true</delivery>', '\t\t\t<pickup>true</pickup>']
        if o['dimensions']:
            lines.append('\t\t\t<dimensions>%s</dimensions>' % o['dimensions'])
        lines += ['\t\t\t<param name="%s">%s</param>' % (xml_escape(n), xml_escape(v)) for n, v in o['params']]
        lines.append('\t\t</offer>')
        out += lines
    out.append('\t\t</offers>')

    colls = OrderedDict()
    if target == 'direct':
        colls = collections_of(roots, subs, offers)
        out.append('\t\t<collections>')
        for cid, c in colls.items():
            out.append('\t\t\t<collection id="%s">' % cid)
            out.append('\t\t\t\t<url>%s</url>' % xml_escape(c['url']))
            out += ['\t\t\t\t<picture>%s</picture>' % xml_escape(p) for p in c['pictures']]
            out.append('\t\t\t\t<name>%s</name>' % xml_escape(c['name']))
            if c['description']:
                out.append('\t\t\t\t<description>%s</description>' % xml_escape(c['description']))
            out.append('\t\t\t</collection>')
        out.append('\t\t</collections>')
    out += ['\t</shop>', '</yml_catalog>', '']

    per_cat = defaultdict(int)
    for o in offers:
        per_cat[o['cat']] += 1
    titles = OrderedDict()
    for ru in used_roots:
        titles[ru] = roots[ru]
        for su in used_subs:
            if subs[su][0] == ru:
                titles[su] = '  ' + subs[su][1]
    stats = {'offers': len(offers), 'per_cat': per_cat, 'titles': titles, 'colls': colls,
             'expected': len([o for o in model(data)[2]]) if target != 'direct' or DIRECT_ROOTS is None
             else len(offers)}
    return '\n'.join(out), stats, warnings


def validate(text, target, check_urls=False):
    """Структурная проверка результата по требованиям площадки. Возвращает список ошибок."""
    errors = []
    try:
        root = ET.fromstring(text.encode('utf-8'))
    except ET.ParseError as e:
        return ['XML не парсится: %s' % e]
    shop = root.find('shop')
    cats = shop.findall('categories/category')
    cat_ids = {c.get('id') for c in cats}
    if len(cat_ids) != len(cats):
        errors.append('Повторяются id категорий')
    for c in cats:
        pid = c.get('parentId')
        if pid and pid not in cat_ids:
            errors.append('parentId %s категории %s не существует' % (pid, c.get('id')))
        if pid and target == 'business':
            errors.append('Категория %s с parentId — для Бизнеса категории плоские' % c.get('id'))
    colls = shop.findall('collections/collection')
    coll_ids = {c.get('id') for c in colls}
    if target == 'direct' and not colls:
        errors.append('Нет блока <collections>')
    if target != 'direct' and colls:
        errors.append('<collections> только для Директа')
    urls = []
    for c in colls:
        cid = c.get('id')
        for tag in ('url', 'name', 'picture'):
            if not (c.findtext(tag) or '').strip():
                errors.append('Каталог %s без %s' % (cid, tag))
        if 'ё' in (c.findtext('name') or '') + (c.findtext('description') or ''):
            errors.append('Каталог %s: буква ё' % cid)
        urls.append(c.findtext('url'))
        urls += [p.text for p in c.findall('picture')]
    ids = set()
    used_cats = set()
    for o in shop.findall('offers/offer'):
        oid = o.get('id')
        if oid in ids:
            errors.append('Оффер %s повторяется' % oid)
        ids.add(oid)
        cid = o.findtext('categoryId')
        used_cats.add(cid)
        if cid not in cat_ids:
            errors.append('Оффер %s: categoryId %s нет в <categories>' % (oid, cid))
        price = o.findtext('price') or ''
        if not re.fullmatch(r'[0-9]+(\.[0-9]+)?', price) or float(price) <= 0:
            errors.append('Оффер %s: цена %r' % (oid, price))
        old = o.findtext('oldprice')
        if old and float(old) <= float(price or 0):
            errors.append('Оффер %s: oldprice не больше price' % oid)
        need = ('name', 'vendor', 'price', 'currencyId', 'categoryId', 'url', 'picture')
        for tag in need + (('description',) if target != 'business' else ()):
            if not (o.findtext(tag) or '').strip():
                errors.append('Оффер %s без %s' % (oid, tag))
        if not (o.findtext('url') or '').startswith(SITE + '/'):
            errors.append('Оффер %s: url не на сайте' % oid)
        cls = [x.text for x in o.findall('collectionId')]
        if target == 'direct':
            if not cls:
                errors.append('Оффер %s без collectionId' % oid)
            for x in cls:
                if x not in coll_ids:
                    errors.append('Оффер %s: collectionId %s нет в <collections>' % (oid, x))
        elif cls:
            errors.append('Оффер %s: collectionId только для Директа' % oid)
        if target == 'business':
            if o.findtext('currencyId') != 'RUB':
                errors.append('Оффер %s: валюта должна быть RUB' % oid)
            if len(o.findall('picture')) != 1:
                errors.append('Оффер %s: должна быть ровно одна picture' % oid)
            if len(oid) > 80 or re.search(r'[^0-9A-Za-zА-Яа-я.,/\\()\[\]=-]', oid):
                errors.append('Оффер %s: недопустимый id' % oid)
            for tag, n in (('name', 250), ('description', 3000), ('shortDescription', 250), ('url', 512)):
                if len(o.findtext(tag) or '') > n:
                    errors.append('Оффер %s: %s длиннее %d' % (oid, tag, n))
            if re.search(r'<[a-z/]', o.findtext('description') or ''):
                errors.append('Оффер %s: HTML в описании' % oid)
        if target == 'tovary':
            # требования Яндекс Товаров (справка merchants/ru: offers, attributes-offer, description)
            if o.get('available') != 'true':
                errors.append('Оффер %s без available="true"' % oid)
            if o.find('manufacturer_warranty') is not None:
                errors.append('Оффер %s: manufacturer_warranty не рекомендован для Товаров' % oid)
            if len(oid) > 20 or re.search(r'[^0-9A-Za-zА-Яа-я.,/\\()\[\]=-]', oid):
                errors.append('Оффер %s: недопустимый id для Товаров' % oid)
            plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', o.findtext('description') or '')).strip()
            if not 70 <= len(plain) <= 3000:
                errors.append('Оффер %s: описание %d знаков (нужно 70–3000)' % (oid, len(plain)))
            if len(o.findtext('name') or '') > 150:
                errors.append('Оффер %s: название длиннее 150 знаков' % oid)
        if check_urls:
            urls.append(o.findtext('url'))
            urls.append(o.findtext('picture'))
    empty = cat_ids - used_cats - {c.get('parentId') for c in cats}
    if empty:
        errors.append('Категории без офферов: %s' % ', '.join(sorted(empty)))
    if check_urls:
        for u in OrderedDict.fromkeys(urls):
            if not head_ok(u):
                errors.append('URL не отвечает 200: %s' % u)
    return errors


def publish(out_paths):
    """Копирует фиды и скрипт в репозиторий feed-github и отправляет на GitHub.
    Сначала подтягивает GitHub: Actions коммитит фиды каждый день, без этого push отклоняется
    (так было 04.11.2026). Связь с github.com бывает нестабильной — сетевые шаги повторяются."""
    def git(*a, timeout=None):
        return subprocess.run(['git', '-C', REPO_DIR] + list(a), capture_output=True, text=True, timeout=timeout)

    def net(*a):
        r = None
        for i in range(3):
            try:
                r = git(*a, timeout=120)
            except subprocess.TimeoutExpired:
                print('git %s: таймаут, попытка %d' % (a[0], i + 1))
                continue
            if not r.returncode:
                return r
            time.sleep(10)
        print('git %s не удался:\n%s' % (' '.join(a), (r.stdout + r.stderr) if r else 'таймаут'))
        sys.exit(2)

    net('pull', '--rebase', '--autostash')
    for out_path in out_paths:
        shutil.copyfile(out_path, os.path.join(REPO_DIR, os.path.basename(out_path)))
    # копия скрипта в репозитории — ее запускает GitHub Actions раз в сутки
    shutil.copyfile(os.path.abspath(__file__), os.path.join(REPO_DIR, 'direct_feed.py'))
    files = [os.path.basename(p) for p in out_paths] + ['direct_feed.py']
    if not git('status', '--porcelain', '--', *files).stdout.strip():
        print('Публикация: изменений нет, на GitHub уже актуальные файлы')
        return
    git('add', '--', *files)
    r = git('commit', '-m', 'Обновление фидов', '--', *files)
    if r.returncode:
        print('git commit не удался:\n' + r.stdout + r.stderr)
        sys.exit(2)
    net('push')
    for out_path in out_paths:
        print('Опубликовано: %s%s' % (PUBLIC_BASE, os.path.basename(out_path)))
    print('GitHub Pages обновится в течение минуты-двух')


def main():
    ap = argparse.ArgumentParser(description='Фиды каталога для Яндекс Директа, Товаров и Бизнеса')
    ap.add_argument('--target', choices=['direct', 'tovary', 'business', 'all'], default='all')
    ap.add_argument('--api-file', '--parts-file', dest='api_file',
                    help='локальная копия ответа API каталога (JSON) вместо запроса')
    ap.add_argument('--out', help='куда писать результат; только при одном --target')
    ap.add_argument('--out-dir', help='папка для результатов вместо «Яндекс Директ/feeds» (GitHub Actions)')
    ap.add_argument('--check-urls', action='store_true', help='проверить HEAD-запросом url и картинки')
    ap.add_argument('--publish', action='store_true', help='скопировать в feed-github и сделать git commit + push')
    args = ap.parse_args()

    targets = list(OUT_FILES) if args.target == 'all' else [args.target]
    if args.out and len(targets) > 1:
        ap.error('--out работает только с одним --target')
    if args.out_dir:
        for t in OUT_FILES:
            OUT_FILES[t] = os.path.join(args.out_dir, os.path.basename(OUT_FILES[t]))

    data = load_catalog(args.api_file)
    written, failed = [], False
    for target in targets:
        out = args.out or OUT_FILES[target]
        result, stats, warnings = build(data, target)
        errors = validate(result, target, check_urls=args.check_urls)
        if stats['offers'] != stats['expected']:
            errors.append('офферов %d, а товаров в разделах фида %d' % (stats['offers'], stats['expected']))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with io.open(out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(result)
        print('=== %s: офферов %d' % (target, stats['offers']))
        for uid, title in stats['titles'].items():
            print('  %s  %-42s %d' % (uid, title, stats['per_cat'].get(uid, 0)))
        if stats['colls']:
            print('Каталоги (collections): %d' % len(stats['colls']))
            for cid, c in stats['colls'].items():
                print('  %s  %s' % (cid, c['name']))
        for w in OrderedDict.fromkeys(warnings):
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
