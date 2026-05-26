from scripts.backfill_public_data import _key_day, _key_month, _parse_s3_list, _zip_keys


def test_binance_s3_listing_parser_filters_zip_keys():
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Contents><Key>data/spot/monthly/klines/AAVEUSDT/1h/AAVEUSDT-1h-2020-10.zip</Key></Contents>
  <Contents><Key>data/spot/monthly/klines/AAVEUSDT/1h/AAVEUSDT-1h-2020-10.zip.CHECKSUM</Key></Contents>
  <NextContinuationToken>abc</NextContinuationToken>
</ListBucketResult>"""

    keys, token = _parse_s3_list(payload)

    assert token == "abc"
    assert _zip_keys(keys) == ["data/spot/monthly/klines/AAVEUSDT/1h/AAVEUSDT-1h-2020-10.zip"]
    assert _key_month(keys[0]) == (2020, 10)


def test_binance_daily_key_date_parser():
    day = _key_day("data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-05-11.zip")

    assert day.year == 2026
    assert day.month == 5
    assert day.day == 11

