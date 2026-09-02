import xml.etree.ElementTree as ET

from app import parse_jma_xml_report, parse_jma_feed


SAMPLE_FEED = '''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>気象警報・注意報（Ｈ２７）</title>
    <link type="application/xml" href="https://example.com/report.xml"/>
    <updated>2026-09-02T06:41:52Z</updated>
  </entry>
</feed>'''

SAMPLE_REPORT = '''<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>青森市気象警報・注意報</Title>
    <ReportDateTime>2026-09-02T15:46:00+09:00</ReportDateTime>
    <InfoType>発表</InfoType>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/">
    <MeteorologicalInfos type="気象警報・注意報">
      <MeteorologicalInfo>
        <Item>
          <Kind>
            <Name>強風注意報</Name>
            <Code>15</Code>
          </Kind>
          <Areas codeType="気象情報／府県予報区・細分区域等">
            <Area>
              <Name>青森市</Name>
              <Code>220100</Code>
            </Area>
          </Areas>
        </Item>
      </MeteorologicalInfo>
    </MeteorologicalInfos>
  </Body>
</Report>'''


def test_parse_jma_feed_extracts_links():
    links = parse_jma_feed(SAMPLE_FEED)
    assert links == ["https://example.com/report.xml"]


def test_parse_jma_xml_report_extracts_warning_for_area():
    warnings, report_datetime = parse_jma_xml_report(SAMPLE_REPORT)
    assert warnings == [{"name": "強風注意報", "code": "15", "status": "発表"}]
    assert report_datetime == "2026-09-02T15:46:00+09:00"


def test_parse_area_warnings_accepts_real_aomori_city_area_code():
    data = [{
        "reportDatetime": "2026-09-02T11:17:00+09:00",
        "warning": {
            "class20Items": [{
                "areaCode": "0220100",
                "kinds": [{"code": "15", "status": "発表"}]
            }]
        }
    }]
    warnings, report_datetime = __import__('app').parse_area_warnings(data)
    assert warnings == [{"name": "強風注意報", "code": "15", "status": "発表"}]
    assert report_datetime == "2026-09-02T11:17:00+09:00"
