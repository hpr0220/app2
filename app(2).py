"""우리집 혜택 알리미 — 단일 화면 Streamlit 앱.
실행: python -m streamlit run app.py
개인정보/비밀번호/API 키를 입력받거나 저장하지 않는다.

[데이터 수정 안내]
1. REAL_BENEFITS에 아래 Benefit 형식으로 항목을 추가한다. id는 영구적으로 고유하게 유지한다.
2. 공식 원문을 직접 확인한 경우에만 source_url, verified_on, 조건/서류를 기록한다.
   verified_on은 실제 확인일이다. 앱 실행일로 자동 갱신하지 않는다.
3. 날짜는 YYYY-MM-DD, 미확인 값은 None. 상시접수가 원문에 명시된 경우만
   period_kind='always'로 설정한다. 기간이 일부라도 불명확하면 'unknown'으로 둔다.
   fixed는 시작/종료일이 모두 확인된 공고에만 쓴다. 마감시각은 별도 period_note에 쓴다.
4. regions={'서울특별시': ('강남구', '서초구')}처럼 제한하거나, 전국 서비스는
   regions={}로 둔다. 첫 버전의 검색 UI 자체는 서울 25개 구만 지원한다.
5. tags는 기본 조건 관련성 검색용이다. 반드시 모두 충족해야 하는 조건은
   required_tags에 별도로 둔다. 여러 tags는 OR, required_tags는 AND이다.
6. age_min/age_max는 사용자가 입력한 본인의 연령이다. 자녀 연령을 넣지 않는다.
7. 예시는 DEMO_BENEFITS에만 넣고 demo=True를 유지한다. 실제 공고와 섞지 않는다.
8. 공식 API/DB를 붙일 때 load_benefits()만 교체하면 화면/필터 코드는 재사용 가능하다.
   API 키는 st.secrets['API_KEY'] 등 Streamlit 비밀정보 설정으로 읽는다.
   실제 API는 이 버전에 연결되어 있지 않다. 사용자 조건을 공용 캐시에 저장하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from io import BytesIO
from urllib.parse import urlsplit
import ipaddress
import re
import streamlit as st

KST = timezone(timedelta(hours=9))
CHECKED_ON = '2026-11-05'  # 아래 공식 원문을 실제 열람한 날짜. 자동 변경 금지.
REGIONS = {'서울특별시': (
    '강남구', '강동구', '강북구', '강서구', '관악구', '광진구', '구로구', '금천구',
    '노원구', '도봉구', '동대문구', '동작구', '마포구', '서대문구', '서초구',
    '성동구', '성북구', '송파구', '양천구', '영등포구', '용산구', '은평구',
    '종로구', '중구', '중랑구',
)}
REGION_SOURCE = 'https://www.seoul.go.kr/seoul/autonomy.do'
AGE_BANDS = {
    '선택 안 함': (None, None), '만 0~18세': (0, 18), '만 19~34세': (19, 34),
    '만 35~49세': (35, 49), '만 50~64세': (50, 64), '만 65~74세': (65, 74),
    '만 75세 이상': (75, 150),
}
TAGS = ['학생', '취업준비생', '직장인', '자영업자', '자녀 양육 가구', '기타']
CATEGORIES = ['생활·복지', '교육', '취업', '주거', '소상공인', '문화']


@dataclass(frozen=True)
class Benefit:
    id: str
    name: str
    summary: str
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    target: str
    extra_checks: tuple[str, ...]
    method: str
    documents: tuple[str, ...]
    agency: str
    source_url: str | None
    verified_on: str | None
    regions: dict[str, tuple[str, ...]]
    period_kind: str = 'unknown'  # always / fixed / unknown
    start: str | None = None
    end: str | None = None
    period_note: str = ''
    age_min: int | None = None
    age_max: int | None = None
    required_tags: tuple[str, ...] = ()
    demo: bool = False


# 실제로 확인한 공식 서비스 2건. 전국 서비스 중 서울 주민이 확인할 수 있는 안내만 제공.
# 금액을 단정하지 않고 공식 원문으로 연결한다. 지역 자체 사업 수집은 아직 하지 않는다.
REAL_BENEFITS = (
    Benefit(
        id='real-training-card', name='국민내일배움카드',
        summary='직업능력개발훈련 수강에 필요한 훈련비의 일부 또는 전액을 지원하는 제도입니다. 과정별 지원 범위와 본인부담금은 공식 안내를 확인하세요.',
        categories=('교육', '취업'), tags=('학생', '취업준비생', '직장인', '자영업자'),
        target='훈련 필요성이 인정되는 사람. 공무원·사립학교 교직원, 만 75세 이상 등은 지원 제외 대상이며 학생·근로자·자영업자에게 별도 제외 기준이 있습니다.',
        extra_checks=(
            '훈련 필요성 심사와 다른 정부지원 훈련 참여 여부',
            '학생: 졸업까지 남은 수업연한, 고등학생은 졸업예정 여부',
            '근로자: 기업 규모·연령·임금, 자영업자: 연 매출, 특수형태근로종사자: 월 소득 등 제외 기준',
            '공무원·사립학교 교직원 해당 여부와 예산·과정별 지원 조건',
        ),
        method='가까운 고용센터 방문 또는 고용24를 통해 신청합니다. 이 앱에서는 신청을 접수하지 않습니다.',
        documents=('직업능력개발계좌(내일배움카드) 발급 신청서', '개인정보 수집·이용 동의서',
                   '본인에게 해당하는 지원대상별 입증 서류(담당 기관 확인)'),
        agency='고용노동부 / 고용센터 · 문의 1350',
        source_url='https://www.gov.kr/portal/service/serviceInfo/149200000026',
        verified_on=CHECKED_ON, regions={}, age_max=74,
        period_note='열람한 공식 안내에서 신청 시작·마감일 또는 상시신청 문구를 확인하지 못했습니다. 고용센터에 접수 가능 여부를 확인하세요.',
    ),
    Benefit(
        id='real-childcare', name='아이돌봄서비스 정부지원',
        summary='양육공백이 있는 가정의 아이돌봄서비스 이용에 대한 정부지원 안내입니다. 서비스 유형과 가구소득 등에 따라 지원 범위가 달라집니다.',
        categories=('생활·복지',), tags=('자녀 양육 가구',), required_tags=('자녀 양육 가구',),
        target='대상아동·양육공백·다른 자녀양육 지원과의 중복금지 기준을 충족하고 소득 판정을 받는 가구. 영아종일제는 생후 3개월~만 36개월, 시간제 등은 생후 3개월~만 12세 아동 기준을 확인합니다.',
        extra_checks=('자녀의 정확한 연령·국적 등 대상아동 기준', '양육공백 인정 여부와 가구소득·가구원 수',
                      '보육료·유아학비·부모급여 등과의 중복지원 제한 및 서비스 시간',
                      '지역 제공기관의 연계 가능 여부와 국민행복카드 준비'),
        method='아동 주소지 읍·면·동 주민센터 방문 또는 복지로 온라인 신청. 온라인 신청 가능 가구에 제한이 있으므로 공식 안내를 확인하세요.',
        documents=('사회보장급여 신청(변경)서', '본인 가구에 해당하는 정부지원 자격 증빙자료',
                   '필요시 양육공백 입증서류(행정정보 연계로 제출이 불필요한 서류는 담당 기관 확인)'),
        agency='아이돌봄서비스 / 주소지 주민센터·시군구 및 지역 서비스 제공기관',
        source_url='https://idolbom.go.kr/front/srvcUtztnGuide/govApply',
        verified_on=CHECKED_ON, regions={},
        period_note='열람한 정부지원 신청 안내에서 확정 신청 기간을 확인하지 못했습니다. 거주지 주민센터·제공기관에 접수 및 연계 가능 여부를 확인하세요.',
    ),
)


def build_demo_benefits() -> tuple[Benefit, ...]:
    # 고정된 기준일에 대한 가상 일정. 실행일마다 마감일을 미루지 않는다.
    # 시간이 지나 예시들이 종료되어도 종료 목록에서 검증할 수 있다.
    base = date(2026, 11, 5)
    cases = (
        ('study', '가상 학습 준비 서비스', ('교육',), ('학생',), 'fixed', -5, 3, {}),
        ('career', '가상 취업 준비 서비스', ('취업',), ('취업준비생',), 'fixed', -3, 18, {}),
        ('home', '가상 주거 상담 서비스', ('주거', '생활·복지'), (), 'always', None, None, {}),
        ('shop', '가상 점포 운영 상담', ('소상공인',), ('자영업자',), 'unknown', None, None, {}),
        ('culture', '가상 마포 문화 체험', ('문화',), (), 'fixed', -20, -1, {'서울특별시': ('마포구',)}),
        ('family', '가상 가족 체험 서비스', ('생활·복지', '문화'), ('자녀 양육 가구',), 'fixed', 10, 30, {}),
    )
    items = []
    for key, title, categories, tags, kind, start_delta, end_delta, regions in cases:
        items.append(Benefit(
            id=f'demo-{key}', name=f'[기능 시연용 예시] {title}',
            summary='실제 존재하거나 신청 가능한 지원사업이 아닙니다. 검색·마감일·관심 혜택·서류 체크를 시험하기 위한 가상 항목입니다.',
            categories=categories, tags=tags, target='시연용으로 정한 조건이며 실제 자격요건이 아닙니다.',
            extra_checks=('가상 예시이므로 실제 신청이나 수혜 판단에 사용할 수 없음',),
            method='시연 전용: 실제 신청 창구가 없습니다.',
            documents=('시연용 준비 항목 A (실제 서류 아님)', '시연용 준비 항목 B (실제 서류 아님)'),
            agency='가상 운영기관 (실존 기관 아님)', source_url=None, verified_on=None,
            regions=regions, period_kind=kind,
            start=(base + timedelta(days=start_delta)).isoformat() if start_delta is not None else None,
            end=(base + timedelta(days=end_delta)).isoformat() if end_delta is not None else None,
            period_note='2026-11-05를 기준으로 작성한 고정된 가상 일정입니다. 공식 공고의 기간이 아닙니다.',
            demo=True,
        ))
    return tuple(items)


DEMO_BENEFITS = build_demo_benefits()


# ── 데이터/판정 계층: Streamlit 화면과 독립적인 함수 ──
def today_kst() -> date:
    return datetime.now(KST).date()


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (ValueError, TypeError):
        return None


def load_benefits(demo: bool) -> list[Benefit]:
    """향후 API/DB 연동 교체 지점. 공식 데이터와 가상 데이터는 절대 합치지 않는다."""
    return list(DEMO_BENEFITS if demo else REAL_BENEFITS)


def period_status(b: Benefit, today: date) -> tuple[str, int | None]:
    start, end = parse_date(b.start), parse_date(b.end)
    # 종료일이 확인되면 일부 날짜 누락 공고도 종료 목록으로 분리한다.
    if start and end and start > end:
        return '기간 확인 필요', None
    if end and end < today:
        return '종료', (end - today).days
    if b.period_kind == 'always' and not b.start and not b.end:
        return '상시 신청', None
    if b.period_kind != 'fixed' or not start or not end:
        return '기간 확인 필요', None
    if today < start:
        return '신청 예정', (start - today).days
    left = (end - today).days
    return ('마감 임박' if left <= 7 else '신청 중'), left


def match_reasons(b: Benefit, province: str, district: str, age: str,
                  tags: list[str], categories: list[str]) -> list[str] | None:
    reasons = []
    if b.regions:
        if province not in b.regions:
            return None
        allowed = b.regions[province]
        if allowed and district not in allowed:
            return None
        reasons.append(f'지역 일치: {province} {district}')
    else:
        reasons.append(f'{province} {district} 주민도 확인할 수 있는 전국 서비스' if not b.demo else '시연 지역 범위 일치')
    lo, hi = AGE_BANDS.get(age, (None, None))
    if lo is not None and hi is not None:
        if (b.age_min is not None and hi < b.age_min) or (b.age_max is not None and lo > b.age_max):
            return None
        if b.age_min is not None or b.age_max is not None:
            reasons.append('선택한 연령대가 안내 연령 범위와 겹침; 정확한 만 나이는 추가 확인 필요')
    elif b.age_min is not None or b.age_max is not None:
        reasons.append('연령을 선택하지 않아 연령 자격은 추가 확인 필요')
    if tags:
        if b.required_tags and not set(b.required_tags).issubset(tags):
            return None
        overlap = sorted(set(tags).intersection(b.tags))
        if b.tags and not overlap:
            return None
        reasons.append('해당 조건 일치: ' + ', '.join(overlap) if overlap else '선택한 상태와 무관하게 살펴볼 수 있는 항목')
    elif b.tags:
        reasons.append('해당 조건 미선택: 대상 조건 확인 필요')
    if categories:
        overlap = [x for x in b.categories if x in categories]
        if not overlap:
            return None
        reasons.append('관심 분야 일치: ' + ', '.join(overlap))
    return reasons


def filter_benefits(items: list[Benefit], today: date, province: str, district: str,
                    age: str, tags: list[str], categories: list[str], query: str,
                    active_only: bool, ended: bool = False) -> list[tuple[Benefit, list[str]]]:
    result = []
    for b in items:
        reasons = match_reasons(b, province, district, age, tags, categories)
        if reasons is None or query.strip().casefold() not in b.name.casefold():
            continue
        status, _ = period_status(b, today)
        if (status == '종료') != ended:
            continue
        if not ended and active_only and status not in ('상시 신청', '신청 중', '마감 임박'):
            continue
        result.append((b, reasons))
    return result


def sort_results(rows: list[tuple[Benefit, list[str]]], today: date, order: str):
    def key(row):
        b = row[0]
        status, _ = period_status(b, today)
        end = parse_date(b.end)
        if status == '종료':
            return (0, -(end.toordinal() if end else 0), b.name)
        if status in ('마감 임박', '신청 중'):
            return (0, end.toordinal(), b.name)
        if status == '신청 예정':
            return (1, (parse_date(b.start) or date.max).toordinal(), b.name)
        return (2 if status == '상시 신청' else 3, date.max.toordinal(), b.name)
    return sorted(rows, key=(lambda row: row[0].name) if order == '혜택명 순' else key)


def validate_public_url(value: str) -> tuple[bool, str]:
    """QR은 접속 URL만 담는다. URL을 서버에서 조회하지 않는다."""
    value = value.strip()
    if not value or len(value) > 1800 or re.search(r'\s', value):
        return False, '공백 없이 HTTPS 주소를 입력해 주세요(최대 1,800자).'
    try:
        parts = urlsplit(value)
        host = parts.hostname or ''
        port = parts.port
        if parts.scheme != 'https' or not host or parts.username or parts.password:
            return False, '아이디·비밀번호가 포함되지 않은 HTTPS 배포 주소가 필요합니다.'
        if parts.query or parts.fragment:
            return False, '개인정보·인증 토큰 노출을 피하려고 ? 또는 # 뒤의 값이 없는 기본 앱 주소만 허용합니다.'
        if port not in (None, 443):
            return False, '기본 HTTPS 포트의 공개 배포 주소를 사용해 주세요.'
        if host == 'localhost' or host.endswith(('.localhost', '.local')) or '.' not in host:
            return False, '다른 스마트폰에서도 열 수 있는 공개 배포 주소를 입력해 주세요.'
        try:
            ipaddress.ip_address(host)
            return False, 'IP 주소 대신 공개 배포 도메인 주소를 입력해 주세요.'
        except ValueError:
            pass
        host.encode('idna')
    except (ValueError, UnicodeError):
        return False, '주소 형식을 확인해 주세요.'
    return True, value


def make_qr_png(url: str) -> bytes:
    import qrcode
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color='#142b49', back_color='white')
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


# ── 화면 계층 ──
CSS = '''
<style>
:root { color-scheme: light; }
.stApp, [data-testid="stHeader"] { background: #ffffff; color: #142b49; }
.block-container { max-width: 850px; padding-top: 1.7rem; padding-bottom: 3rem; }
h1,h2,h3 { color: #142b49 !important; letter-spacing: -.035em; }
h1 { font-size: 2rem !important; } h2 { font-size: 1.45rem !important; }
h3 { font-size: 1.18rem !important; }
p, li, label, [data-testid="stMarkdownContainer"] { line-height: 1.7; }
[data-testid="stCaptionContainer"] { color: #52677a !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 16px; }
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-linkButton"], [data-testid="stBaseButton-downloadButton"]
{ min-height: 46px; border-radius: 10px; }
[data-baseweb="input"], [data-baseweb="select"] > div { min-height: 46px; }
[data-testid="stCheckbox"] label { min-height: 44px; align-items: center; }
.badge { display: inline-block; padding: .22rem .7rem; margin: .2rem .3rem .2rem 0;
 border-radius: 24px; font-size: .94rem; font-weight: 700; }
.b-open { background: #d9f3eb; color: #135844; }
.b-urgent { background: #ffeadc; color: #853d13; }
.b-check { background: #fff3cc; color: #654c00; }
.b-muted { background: #e9eef4; color: #354b64; }
.intro { border-left: 5px solid #55bba8; padding: .6rem 1rem;
 background: #f1faf7; border-radius: 0 12px 12px 0; margin-bottom: 1rem; }
@media(max-width: 640px) {
 .block-container { padding: 1rem 1rem 2rem; }
 h1 { font-size: 1.7rem !important; }
}
</style>
'''


def remember_checked(bid: str, index: int, widget_key: str):
    # 체크박스가 화면에서 사라져도 독립적인 세션 상태에는 유지한다.
    st.session_state['document_checks'][(bid, index)] = bool(st.session_state[widget_key])


def render_documents(b: Benefit, location: str):
    if not b.documents:
        st.info('확인된 준비서류가 없습니다. 담당 기관에서 최신 목록을 확인하세요.')
        return
    st.caption('모든 서류가 모든 신청자에게 필요한 것은 아닙니다. 실제 파일이나 개인정보는 업로드하지 마세요.')
    checks = st.session_state['document_checks']
    for i, doc in enumerate(b.documents):
        key = f'doc_{location}_{b.id}_{i}'
        st.session_state[key] = checks.get((b.id, i), False)
        st.checkbox(doc, key=key, on_change=remember_checked, args=(b.id, i, key))
    count = sum(bool(checks.get((b.id, i), False)) for i in range(len(b.documents)))
    st.caption(f'준비 체크 {count}/{len(b.documents)} · 자격 심사나 서류 제출이 완료되었다는 뜻이 아닙니다.')


def render_card(b: Benefit, reasons: list[str], today: date, location: str):
    status, days = period_status(b, today)
    badge_class = ('b-urgent' if status == '마감 임박' else 'b-open'
                   if status in ('상시 신청', '신청 중') else 'b-muted')
    with st.container(border=True):
        st.subheader(b.name)
        st.markdown(f'<span class="badge {badge_class}">{escape(status)}</span>'
                    '<span class="badge b-check">추가 확인 필요</span>', unsafe_allow_html=True)
        st.write(b.summary)
        if status in ('마감 임박', '신청 중'):
            left_text = '오늘 마감 (마감시각 확인 필요)' if days == 0 else f'마감까지 {days}일'
            st.write(f'**신청 기간:** {b.start} ~ {b.end} · **{left_text}**')
        elif status == '신청 예정':
            st.write(f'**신청 기간:** {b.start} ~ {b.end} · 시작까지 {days}일')
        elif status == '종료':
            st.write(f'**신청 기간:** {b.start or "시작일 미확인"} ~ {b.end} · 종료된 공고')
        elif status == '상시 신청':
            st.write('**신청 기간:** 상시 신청 · 고정 마감일 없음')
        else:
            st.write('**신청 기간 / 남은 날짜:** 기간 확인 필요')
            if b.start or b.end:
                st.caption(f'기록된 날짜: 시작 {b.start or "미확인"} / 마감 {b.end or "미확인"} (전체 기간 미확정)')
        st.write('**보여주는 이유:** ' + ' / '.join(reasons))
        st.caption('분야: ' + ' · '.join(b.categories))
        saved = b.id in st.session_state['favorites']
        if st.button('★ 관심 혜택에서 빼기' if saved else '☆ 관심 혜택에 담기',
                     key=f'save_{location}_{b.id}', use_container_width=True):
            if saved:
                st.session_state['favorites'].discard(b.id)
            else:
                st.session_state['favorites'].add(b.id)
            st.rerun()
        with st.expander('대상·신청 방법·준비서류 확인'):
            st.markdown('**대상 및 주요 조건**')
            st.write(b.target)
            st.markdown('**추가 확인 필요**')
            for text in b.extra_checks or ('소득·재산·가구 구성·중복 수혜 등 공식 공고의 최종 조건',):
                st.write('• ' + text)
            st.caption('이 앱은 소득·재산·가구 구성·정확한 만 나이·중복 수혜 여부를 심사하지 않습니다.')
            st.markdown('**기간 관련 안내**')
            st.write(b.period_note or '공식 공고에서 예산 소진·마감시각·변경 공지를 확인하세요.')
            st.markdown('**신청 방법**')
            st.write(b.method)
            st.markdown('**준비서류 체크**')
            render_documents(b, location)
        st.write('**담당 기관:** ' + b.agency)
        if b.demo:
            st.caption('정보 확인일: 해당 없음 · 기능 시연용 가상 데이터 · 공식 안내 링크 없음')
        else:
            checked = parse_date(b.verified_on)
            st.caption(f'정보 확인일: {b.verified_on if checked else "확인일 미등록"} · 실시간 자동 갱신 아님')
            if checked and (today - checked).days > 30:
                st.warning('확인일로부터 30일이 넘었습니다. 현재 조건과 접수 여부를 공식 안내에서 다시 확인하세요.')
            if b.source_url:
                ok, _ = validate_public_url(b.source_url)
                if ok:
                    st.link_button('공식 안내에서 최종 확인 ↗', b.source_url, use_container_width=True)
                else:
                    st.warning('공식 링크 형식 확인이 필요합니다. 담당 기관에 문의하세요.')
            else:
                st.warning('공식 안내 링크가 등록되지 않았습니다. 담당 기관에 확인하세요.')


def render_qr():
    with st.expander('QR코드 만들기 · 배포 주소 공유'):
        st.write('배포가 끝난 앱의 HTTPS 주소를 넣으세요. QR코드는 보안장치가 아니라 접속 주소입니다.')
        st.caption('URL은 QR 생성에만 사용하며 앱에서 해당 사이트를 조회하지 않습니다. 개인정보나 비밀키를 넣지 마세요.')
        url = st.text_input('배포된 앱 주소', placeholder='https://your-app.streamlit.app', key='qr_address')
        if url:
            valid, value = validate_public_url(url)
            if not valid:
                st.warning(value)
            else:
                try:
                    png = make_qr_png(value)
                    st.image(png, caption='배포 주소 접속용 QR코드', width=240)
                    st.code(value, language=None)
                    st.download_button('QR코드 PNG 내려받기', data=png, file_name='our_home_benefits_qr.png',
                                       mime='image/png', use_container_width=True)
                    st.caption('QR 생성은 배포 성공이나 접속 가능 여부를 검증하지 않습니다. 휴대폰으로 직접 스캔해 확인하세요. 비공개 앱은 별도 접근 권한이 필요합니다.')
                except Exception:
                    st.error('QR 이미지를 만들지 못했습니다. requirements.txt 설치 여부와 주소 길이를 확인해 주세요.')


def main():
    st.set_page_config(page_title='우리집 혜택 알리미', page_icon='🏡', layout='centered',
                       initial_sidebar_state='collapsed')
    st.markdown(CSS, unsafe_allow_html=True)
    st.session_state.setdefault('favorites', set())
    st.session_state.setdefault('document_checks', {})
    today = today_kst()
    st.title('🏡 우리집 혜택 알리미')
    st.markdown('<div class="intro">몰라서 놓치는 혜택을 줄이는 작은 행정 길잡이<br>'
                '행정직 공무원을 꿈꾸며 만든 <b>주민 행정정보 접근성 개선 학습 프로젝트</b>입니다.</div>', unsafe_allow_html=True)
    st.caption('정부·지자체가 운영하는 공식 서비스가 아닙니다. 이름·주민등록번호를 입력받지 않습니다.')
    st.caption('첫 버전: 서울특별시 25개 자치구 · 공식 서비스 2건(전국 사업 중 서울 주민 안내). 자치구별 자체 사업은 아직 수록하지 않았습니다.')

    mode = st.selectbox('데이터 모드', ['공식 안내 확인 데이터', '기능 시연용 예시 (가상)'])
    demo = mode.startswith('기능')
    if demo:
        st.warning('기능 시연용 예시만 표시합니다. 이름·기관·조건·일정 모두 가상이며 실제 신청할 수 없습니다. 공식 데이터와 섞지 않습니다.')
    else:
        st.info('기본 조건에 맞춰 확인해 볼 만한 혜택을 안내합니다. 최종 자격은 공식 공고와 담당 기관에서 확인하세요. 신청 기간이 확인되지 않은 2건은 접수 중이라고 표시하지 않습니다.')

    c1, c2 = st.columns(2)
    with c1:
        province = st.selectbox('거주 시·도', list(REGIONS))
    with c2:
        district = st.selectbox('거주 시·군·구', REGIONS[province])
    age = st.selectbox('본인의 연령대 (만 나이 기준)', list(AGE_BANDS))
    with st.expander('해당 조건·관심 분야 선택 (선택 사항)'):
        tags = st.multiselect('해당 조건 · 복수 선택 가능', TAGS)
        categories = st.multiselect('관심 분야 · 복수 선택 가능', CATEGORIES)
        st.caption('아무것도 선택하지 않으면 넓게 찾아봅니다. 해당 조건은 하나 이상 관련되면 표시하며, 자녀 양육처럼 필수 조건이 지정된 항목은 함께 확인합니다. 연령은 본인 기준이며 자녀 연령은 수집하지 않습니다.')
        st.caption('연령대 일부만 대상과 겹쳐도 표시될 수 있습니다. 이는 수혜 자격 확정이 아닙니다.')
    query = st.text_input('혜택명 검색', placeholder='예: 내일배움, 아이돌봄', max_chars=100)
    c1, c2 = st.columns(2)
    with c1:
        active_only = st.checkbox('신청 중인 혜택만 보기', value=False)
    with c2:
        order = st.selectbox('정렬', ['마감 가까운 순', '혜택명 순'])
    st.caption(f'오늘: {today.isoformat()} (한국 시간) · 마감순: 접수 중 → 신청 예정 → 상시 → 기간 미확인. 실제 마감시각·예산 소진 여부는 공식 확인이 필요합니다.')
    if active_only:
        st.caption('기간 미확인·신청 예정·종료 항목은 제외합니다. 상시 신청은 포함합니다. 결과가 없다면 이 옵션을 해제하세요.')
    items = load_benefits(demo)
    args = dict(items=items, today=today, province=province, district=district, age=age,
                tags=tags, categories=categories, query=query, active_only=active_only)
    current = sort_results(filter_benefits(**args), today, order)
    closed = sort_results(filter_benefits(**args, ended=True), today, order)
    st.header(f'확인해 볼 만한 혜택 {len(current)}건')
    if not items:
        st.info('아직 등록된 혜택 정보가 없습니다. 운영자가 공식 안내를 확인해 등록하면 이곳에 표시됩니다.')
    elif not current:
        st.info('현재 선택 조건과 일치하는 등록 항목이 없습니다. 검색어를 지우거나, 관심 분야·해당 조건을 줄이거나, 신청 중 필터를 해제해 보세요. 이 앱에 없다고 실제 혜택이 없는 것은 아닙니다.')
    for b, reasons in current:
        render_card(b, reasons, today, 'results')
    with st.expander(f'종료된 공고 별도로 보기 · {len(closed)}건'):
        st.caption('위의 지역·연령·조건·분야·검색어를 적용합니다. 이 영역은 종료 공고이므로 신청 중 필터를 적용하지 않습니다.')
        if not closed:
            st.write('현재 조건에 맞는 종료 공고가 없습니다.')
        for b, reasons in closed:
            render_card(b, reasons, today, 'closed')

    selected = [b for b in items if b.id in st.session_state['favorites']]
    with st.expander(f'★ 내 관심 혜택 모아 보기 · 현재 모드 {len(selected)}건'):
        st.caption('현재 모드에 담은 항목을 검색 조건과 무관하게 표시합니다. 다른 모드의 관심 항목은 해당 모드로 바꾸면 다시 보입니다.')
        if not selected:
            st.write('혜택 카드의 ‘관심 혜택에 담기’를 눌러 모아 보세요.')
        for b in selected:
            render_card(b, ['직접 관심 혜택으로 선택한 항목 · 현재 검색 조건과 무관'], today, 'favorites')

    st.caption('관심 혜택과 준비서류 체크는 현재 접속 세션에서만 유지됩니다. 새로고침·연결 해제·앱 재시작 시 사라질 수 있고 기기 간 동기화되지 않습니다. 서류 파일은 수집하지 않습니다.')
    render_qr()
    with st.expander('운영 범위·정보 출처·확장 기능 안내'):
        st.write('이 앱의 ‘알리미’는 접속했을 때 혜택과 마감일을 보여주는 기능입니다. 문자·이메일·푸시 알림을 보내지 않으며 실제 신청·자격 판정도 처리하지 않습니다.')
        st.write('데이터는 app.py에 수동으로 관리합니다. 공식 안내 확인일 이후 변경사항이 있을 수 있으며 전국 모든 혜택을 수집하지 않습니다.')
        st.markdown(f'[서울시 자치구 목록 출처]({REGION_SOURCE}) · 확인일 {CHECKED_ON}')
        st.write('혜택별 공식 원문 링크와 확인일은 각 카드에 표시합니다. 지원금 금액은 확인 없이 생성하지 않습니다. 소득·재산·가구 구성·중복 수혜 등은 추가 확인이 필요합니다.')
        st.write('확장 제안: 공식 API 연동, 공고 변경 이력, 쉬운 말·다국어 안내, 동의 기반 마감 알림. 영구 저장은 별도 저장소와 개인정보 보호 설계가 필요하고, 자동 알림은 외부 발송 서비스·스케줄러·수신 동의가 필요합니다.')
        st.write('키가 필요한 경우 Streamlit Community Cloud 앱 설정의 Secrets에 저장하고 코드에서는 st.secrets로 읽으세요. 비밀번호·API 키·개인정보를 공개 GitHub에 올리지 마세요. 이 버전은 API 키가 필요 없습니다.')


if __name__ == '__main__':
    main()
