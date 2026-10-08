"""Каталог услуг для кнопки «📝 Заказать» (Э4). Только ключ → короткое название.
Цен здесь нет намеренно: цены живут только в прайсе (data/kb/Прайс_и_условия.md)."""

SERVICES: dict[str, str] = {
    "wfoe": "регистрация WFOE",
    "accounting": "бухгалтерия",
    "accounting_switch": "переход на нашу бухгалтерию",
    "hk_company": "компания в Гонконге",
    "rep_office": "представительство",
    "export_license": "экспортная лицензия",
    "alipay_wechat": "Alipay + WeChat Pay",
    "sourcing_express": "экспресс-подбор поставщиков",
    "sourcing_pro": "расширенный подбор поставщиков",
    "supplier_check": "проверка поставщика",
    "factory_visit": "выездная проверка",
    "buyout": "выкуп товара",
    "customs": "таможенное оформление",
}


def service_title(key: str | None) -> str | None:
    return SERVICES.get(key or "")
