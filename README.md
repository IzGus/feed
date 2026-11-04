YML-фиды магазина «Электромонтажник» для Яндекса.

- elektromontazhnik_direct.yml — Яндекс Директ (ЕПК): https://izgus.github.io/feed/elektromontazhnik_direct.yml
- elektromontazhnik_tovary.xml — Яндекс Товары / Вебмастер: https://izgus.github.io/feed/elektromontazhnik_tovary.xml
- elektromontazhnik_business.xml — Яндекс Бизнес (прайс-лист профиля): https://izgus.github.io/feed/elektromontazhnik_business.xml

Источник — открытое API каталога Тильды. Во всех трех фидах весь каталог (у кампании Директа на генераторы
стоит фильтр по категории).

Каждый день в 06:00 по Москве GitHub Actions пересобирает фиды копией скрипта direct_feed.py.

Папка astari/ — фото и CSV импорта товаров Астари в Тильду.

Файлы собираются скриптом scripts/direct_feed.py в проекте D:\Montazhnik и публикуются командой:

    PYTHONIOENCODING=utf-8 python scripts/direct_feed.py --check-urls --publish
