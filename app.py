import xml.etree.ElementTree as ET

from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
import re
import uuid
from datetime import datetime, timedelta, timezone
from werkzeug.utils import secure_filename

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 気象庁の市区町村コードは 7 桁の形式（例: 0220100）で返ることがある。
# 互換性のため、入力値を正規化して 220100 と 0220100 の両方に対応させる。
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)


def normalize_area_code(code):
    """JMAの市区町村コードの表記ゆれを吸収する"""
    if code is None:
        return ""
    return str(code).strip().lstrip("0")


def matches_area_code(code):
    """対象地域の市区町村コードに一致するか判定する"""
    return normalize_area_code(code) == normalize_area_code(AREA_CODE)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
UPLOAD_DIR = os.path.join(APP_DIR, 'static', 'uploads')
MAX_IMAGE_SIZE = 5 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
ALLOWED_IMAGE_MIMES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_shelters():
    """避難所データをファイルに保存する"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(shelters, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def shelter_form_values(source=None):
    """フォーム表示用に避難所データの欠損項目を空文字で補う"""
    source = source or {}
    fields = (
        'name', 'status', 'address', 'capacity', 'occupants', 'consideration_count',
        'english_support', 'phone', 'email', 'pet', 'supplies', 'latitude',
        'longitude', 'photo'
    )
    return {field: source.get(field, '') for field in fields}


def validate_shelter_form(form, image):
    """避難所登録・更新フォームの値を検証する"""
    values = {key: form.get(key, '').strip() for key in form.keys()}
    required_fields = {
        'name': '避難所名', 'status': '避難所ステータス', 'address': '住所',
        'capacity': '収容人数', 'occupants': '現在の入所者',
        'consideration_count': '要配慮者人数', 'english_support': '避難所英語対応',
        'phone': '避難所連絡先（電話）', 'email': '避難所連絡先（メール）',
        'pet': 'ペット同伴', 'supplies': '物資状況',
    }
    errors = {
        field: f'{label}を入力してください。'
        for field, label in required_fields.items()
        if not values.get(field)
    }

    choices = {
        'status': {'通常', '一時的に閉鎖', '要確認'},
        'english_support': {'可', '不可'},
        'pet': {'可', '不可'},
        'supplies': {'十分', '不足'},
    }
    for field, allowed in choices.items():
        if values.get(field) and values[field] not in allowed:
            errors[field] = '選択肢から選んでください。'

    integer_fields = ('capacity', 'occupants', 'consideration_count')
    for field in integer_fields:
        if values.get(field):
            try:
                if int(values[field]) < 0:
                    raise ValueError
            except ValueError:
                errors[field] = '0以上の整数を入力してください。'

    if values.get('capacity') and values.get('occupants'):
        try:
            if int(values['occupants']) > int(values['capacity']):
                errors['occupants'] = '収容人数以下の人数を入力してください。'
        except ValueError:
            pass

    if values.get('phone') and not re.fullmatch(r'[0-9０-９+()（）\-\s]{7,20}', values['phone']):
        errors['phone'] = '電話番号の形式が正しくありません。'
    if values.get('email') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
        errors['email'] = 'メールアドレスの形式が正しくありません。'

    if values.get('latitude'):
        try:
            if not -90 <= float(values['latitude']) <= 90:
                raise ValueError
        except ValueError:
            errors['latitude'] = '緯度の値が正しくありません。'
    if values.get('longitude'):
        try:
            if not -180 <= float(values['longitude']) <= 180:
                raise ValueError
        except ValueError:
            errors['longitude'] = '経度の値が正しくありません。'

    image_data = None
    if image and image.filename:
        original_name = secure_filename(image.filename)
        extension = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
        image_data = image.read(MAX_IMAGE_SIZE + 1)
        image.seek(0)
        signatures = {
            'image/jpeg': image_data.startswith(b'\xff\xd8\xff'),
            'image/png': image_data.startswith(b'\x89PNG\r\n\x1a\n'),
            'image/gif': image_data.startswith((b'GIF87a', b'GIF89a')),
            'image/webp': image_data.startswith(b'RIFF') and image_data[8:12] == b'WEBP',
        }
        if extension not in ALLOWED_IMAGE_EXTENSIONS or image.mimetype not in ALLOWED_IMAGE_MIMES:
            errors['photo'] = 'JPEG、PNG、GIF、WebP形式の画像を選択してください。'
        elif len(image_data) > MAX_IMAGE_SIZE:
            errors['photo'] = '画像は5MB以下にしてください。'
        elif not signatures.get(image.mimetype, False):
            errors['photo'] = '画像ファイルの内容を確認できません。'

    return values, errors, image_data
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def demo_shelters():
    """検索画面の簡易デモ用避難所データを返す"""
    return [
        {
            "id": 1,
            "name": "○○小学校",
            "address": "青森市大野1-2-3",
            "status": "開設中",
            "crowding": "空いている",
            "phone": "090-0000-0001",
            "support": "対応あり",
            "pet": "対応あり",
            "note": "医療支援や車椅子対応が可能です。",
        },
        {
            "id": 2,
            "name": "△△中学校",
            "address": "青森市栄町4-5-6",
            "status": "開設中",
            "crowding": "やや混雑",
            "phone": "090-0000-0002",
            "support": "対応あり",
            "pet": "対応あり",
            "note": "自治体職員が常駐しています。",
        },
        {
            "id": 3,
            "name": "□□公民館",
            "address": "青森市新町7-8-9",
            "status": "一部開設",
            "crowding": "混雑",
            "phone": "090-0000-0003",
            "support": "対応あり",
            "pet": "未対応",
            "note": "ペット連れの方は別スペースを案内します。",
        },
    ]


def normalize_search_text(value):
    """検索文字列の表記ゆれを吸収する"""
    if value is None:
        return ""
    return "".join(str(value).lower().split())


def search_shelters(keyword=None, address=None, name=None):
    """住所または避難所名で簡易検索する"""
    query = keyword or address or name or ""
    query = normalize_search_text(query)
    candidates = demo_shelters()

    if not query:
        return candidates

    filtered = []
    for shelter in candidates:
        haystack = "".join([
            shelter.get('name', ''),
            shelter.get('address', ''),
            shelter.get('status', ''),
        ])
        if query in normalize_search_text(haystack):
            filtered.append(shelter)
    return filtered or []


def parse_jma_feed(feed_data):
    """Atom フィードから XML レポートへのリンクを抽出する"""
    if feed_data is None:
        return []

    root = ET.fromstring(feed_data)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    links = []
    for entry in root.findall("a:entry", ns):
        link = entry.find("a:link", ns)
        if link is not None:
            href = link.attrib.get("href")
            if href:
                links.append(href)
    return links


def parse_jma_xml_report(xml_data):
    """XML形式の気象警報から対象地域の警報・注意報を抽出する"""
    if xml_data is None:
        return [], ""

    root = ET.fromstring(xml_data)
    ns = {
        "jmaxml": "http://xml.kishou.go.jp/jmaxml1/",
        "basis": "http://xml.kishou.go.jp/jmaxml1/informationBasis1/",
        "met": "http://xml.kishou.go.jp/jmaxml1/body/meteorology1/",
    }

    head = root.find("{http://xml.kishou.go.jp/jmaxml1/informationBasis1/}Head")
    report_datetime = ""
    if head is not None:
        report_datetime = head.findtext("{http://xml.kishou.go.jp/jmaxml1/informationBasis1/}ReportDateTime", default="")

    info_type = ""
    if head is not None:
        info_type = head.findtext("{http://xml.kishou.go.jp/jmaxml1/informationBasis1/}InfoType", default="")

    warnings = []
    seen_codes = set()
    for item in root.findall(".//met:Item", ns):
        kind = item.find("met:Kind", ns)
        if kind is None:
            continue

        code = kind.findtext("met:Code", default="", namespaces=ns)
        name = kind.findtext("met:Name", default="", namespaces=ns)
        if not code or not name:
            continue

        areas = item.findall("met:Areas/met:Area", ns)
        matched = False
        for area in areas:
            area_name = area.findtext("met:Name", default="", namespaces=ns)
            area_code = area.findtext("met:Code", default="", namespaces=ns)
            if area_name == AREA_NAME or matches_area_code(area_code):
                matched = True
                break
        if not matched:
            continue

        if code in seen_codes:
            continue
        warnings.append({
            "name": name,
            "code": code,
            "status": info_type or "発表",
        })
        seen_codes.add(code)

    return warnings, report_datetime


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        for items_key in ("class20Items", "class10Items"):
            items = warning.get(items_key, [])
            if not isinstance(items, list):
                continue

            area = next(
                (
                    item for item in items
                    if isinstance(item, dict) and matches_area_code(item.get("areaCode"))
                ),
                None,
            )
            if not area:
                continue

            kinds = area.get("kinds", [])
            if not isinstance(kinds, list):
                continue

            for kind in kinds:
                if not isinstance(kind, dict):
                    continue

                status = kind.get("status", "")
                code = kind.get("code", "")
                if status not in ("発表", "継続", "発表警報・注意報はなし") or not code or code in seen_codes:
                    if status == "発表警報・注意報はなし":
                        continue
                    continue

                warnings.append({
                    "name": WARNING_CODES.get(
                        code,
                        f"不明な警報・注意報 (コード: {code})"
                    ),
                    "code": code,
                    "status": status
                })
                seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template('index.html', resident_notices=resident_notices, shelters=shelters)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@app.route('/shelter_register/<int:shelter_id>', methods=['GET', 'POST'])
@login_required
def shelter_register(shelter_id=None):
    existing = next((s for s in shelters if s.get('id') == shelter_id), None)
    if shelter_id is not None and existing is None:
        return '避難所が見つかりません。', 404

    if request.method == 'POST':
        values, errors, image_data = validate_shelter_form(
            request.form, request.files.get('photo')
        )
        duplicate = next(
            (
                shelter for shelter in shelters
                if shelter.get('name', '').strip().casefold() == values.get('name', '').casefold()
                and shelter.get('id') != shelter_id
            ),
            None,
        )
        if duplicate:
            errors['name'] = '同じ避難所名がすでに登録されています。'

        if errors:
            values['photo'] = existing.get('photo', '') if existing else ''
            return render_template(
                'shelter_register.html',
                error=True,
                message='入力内容を確認してください。',
                field_errors=errors,
                shelter=values,
                editing=existing is not None,
            )

        now = get_japan_time()
        shelter_values = dict(existing) if existing else shelter_form_values()
        shelter_values.update({
            'name': values['name'],
            'status': values['status'],
            'address': values['address'],
            'capacity': int(values['capacity']),
            'occupants': int(values['occupants']),
            'consideration_count': int(values['consideration_count']),
            'english_support': values['english_support'],
            'phone': values['phone'],
            'email': values['email'],
            'pet': values['pet'],
            'supplies': values['supplies'],
            'latitude': values.get('latitude', ''),
            'longitude': values.get('longitude', ''),
            'updated_at': now,
        })

        if existing is None:
            shelter_values.update({
                'id': max((s.get('id', 0) for s in shelters), default=0) + 1,
                'created_at': now,
            })
            shelters.append(shelter_values)
        else:
            if request.form.get('delete_photo') == 'on':
                shelter_values['photo'] = ''
            existing.clear()
            existing.update(shelter_values)

        image = request.files.get('photo')
        if image_data and image and image.filename:
            extension = secure_filename(image.filename).rsplit('.', 1)[-1].lower()
            filename = f'{uuid.uuid4().hex}.{extension}'
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            image.save(os.path.join(UPLOAD_DIR, filename))
            shelter_values['photo'] = f'/static/uploads/{filename}'
            if existing is not None:
                existing['photo'] = shelter_values['photo']
        save_shelters()
        name = shelter_values['name']
        action = '更新' if existing is not None else '登録'
        return render_template(
            'shelter_register.html', success=True,
            message=f'{name}を{action}しました。',
            shelter=shelter_values,
            editing=existing is not None,
        )

    return render_template(
        'shelter_register.html',
        shelter=shelter_form_values(existing),
        editing=existing is not None,
    )

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html', shelters=shelters)

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters, managed_list=True)


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    if request.method == 'POST':
        action = request.form.get('action', 'create')

        if action == 'delete':
            try:
                instruction_id = int(request.form.get('id', ''))
            except ValueError:
                instruction_id = None

            if instruction_id is not None:
                instructions[:] = [
                    instruction for instruction in instructions
                    if instruction.get('id') != instruction_id
                ]
                save_instructions()
            return redirect(url_for('board'))

        message_type = request.form.get('message_type', '').strip()
        content = request.form.get('content', '').strip()
        if message_type in ('damage', 'evacuation') and content:
            now = get_japan_time()
            instruction = {
                'id': max((i.get('id', 0) for i in instructions), default=0) + 1,
                'target': '住民',
                'message_type': message_type,
                'content': content,
                'region': request.form.get('region', '').strip(),
                'audience': request.form.get('audience', '').strip(),
                'shelter': request.form.get('shelter', '').strip(),
                'status': '発信',
                'created_at': now,
                'updated_at': now,
            }
            instructions.insert(0, instruction)
            save_instructions()
        return redirect(url_for('board'))

    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template('board.html', instructions=resident_instructions)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    keyword = request.args.get('keyword') or request.args.get('address') or request.args.get('name') or ""
    results = search_shelters(keyword)
    return render_template('search_results.html', results=results, keyword=keyword)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
