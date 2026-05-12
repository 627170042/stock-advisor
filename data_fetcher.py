"""
A股T+1短线选股系统 - 数据采集模块
数据源：新浪财经API（实时行情）+ 历史K线
"""
import requests
import json
import time
import os
import re
from datetime import datetime, timedelta
from functools import lru_cache

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn'
}

# ==================== 实时行情 ====================

def get_realtime_quote(code):
    """获取单只股票实时行情 code: sh600519 或 sz000001"""
    url = f'https://hq.sinajs.cn/list={code}'
    r = requests.get(url, headers=HEADERS, timeout=10)
    match = re.search(r'="([^"]+)"', r.text)
    if not match:
        return None
    fields = match.group(1).split(',')
    if len(fields) < 32:
        return None
    return {
        'code': code,
        'name': fields[0],
        'open': float(fields[1]),
        'prev_close': float(fields[2]),
        'current': float(fields[3]),
        'high': float(fields[4]),
        'low': float(fields[5]),
        'volume': int(fields[8]),
        'amount': float(fields[9]),
        'date': fields[30],
        'time': fields[31],
    }

def get_batch_quotes(codes):
    """批量获取实时行情"""
    codes_str = ','.join(codes)
    url = f'https://hq.sinajs.cn/list={codes_str}'
    r = requests.get(url, headers=HEADERS, timeout=15)
    results = {}
    for line in r.text.strip().split('\n'):
        match = re.search(r'hq_str_(s[hz]\d+)="([^"]+)"', line)
        if not match:
            continue
        code = match.group(1)
        fields = match.group(2).split(',')
        if len(fields) < 32 or fields[0] == '':
            continue
        try:
            results[code] = {
                'code': code,
                'name': fields[0],
                'open': float(fields[1]),
                'prev_close': float(fields[2]),
                'current': float(fields[3]),
                'high': float(fields[4]),
                'low': float(fields[5]),
                'volume': int(fields[8]),
                'amount': float(fields[9]),
                'date': fields[30],
                'time': fields[31],
                'change_pct': round((float(fields[3]) - float(fields[2])) / float(fields[2]) * 100, 2) if float(fields[2]) > 0 else 0,
            }
        except (ValueError, ZeroDivisionError):
            continue
    return results


# ==================== 股票列表（多维度排序） ====================

def get_stock_list(page=1, num=80, sort='amount', asc=0, node='hs_a'):
    """获取A股列表，可按不同维度排序"""
    url = f'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
    params = {
        'page': page,
        'num': num,
        'sort': sort,
        'asc': asc,
        'node': node,
        '_s_r_a': 'auto'
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    try:
        data = r.json()
    except:
        return []
    results = []
    for item in data:
        try:
            results.append({
                'symbol': item['symbol'],
                'code': item['code'],
                'name': item['name'],
                'trade': float(item['trade']),
                'pricechange': float(item['pricechange']),
                'changepercent': float(item['changepercent']),
                'buy': float(item['buy']) if item['buy'] else 0,
                'sell': float(item['sell']) if item['sell'] else 0,
                'settlement': float(item['settlement']),
                'open': float(item['open']),
                'high': float(item['high']),
                'low': float(item['low']),
                'volume': int(item['volume']),
                'amount': float(item['amount']),
                'turnoverratio': float(item.get('turnoverratio', 0)),
                'per': float(item.get('per', 0)),
                'pb': float(item.get('pb', 0)),
                'mktcap': float(item.get('mktcap', 0)),
                'nmc': float(item.get('nmc', 0)),
            })
        except (ValueError, KeyError):
            continue
    return results


def get_top_gainers(num=80):
    """获取涨幅前N的股票"""
    return get_stock_list(page=1, num=num, sort='changepercent', asc=0)


def get_top_volume(num=80):
    """获取成交额前N的股票"""
    return get_stock_list(page=1, num=num, sort='amount', asc=0)


def get_top_turnover(num=80):
    """获取换手率前N的股票（资金活跃度）"""
    return get_stock_list(page=1, num=num, sort='turnoverratio', asc=0)


def get_top_rise_volume(num=80):
    """获取量比前N的股票（资金流入加速）"""
    return get_stock_list(page=1, num=num, sort='volume', asc=0)


# ==================== 历史K线 ====================

def get_kline_sina(code, scale='240', datalen='30'):
    """
    获取新浪K线数据
    code: sh600519
    scale: 5/15/30/60/240(日K)
    datalen: 返回条数
    """
    url = f'https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData'
    params = {
        'symbol': code,
        'scale': scale,
        'ma': 'no',
        'datalen': datalen
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        text = r.text
        match = re.search(r'\((\[.*\])\)', text, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
            result = []
            for item in data:
                result.append({
                    'day': item['day'],
                    'open': float(item['open']),
                    'high': float(item['high']),
                    'low': float(item['low']),
                    'close': float(item['close']),
                    'volume': int(item['volume']),
                })
            return result
    except Exception as e:
        print(f"K线获取失败 {code}: {e}")
    return []


# ==================== 辅助函数 ====================

def is_trading_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.strftime('%H%M')
    return '0930' <= t <= '1500'


def get_market_status():
    now = datetime.now()
    if now.weekday() >= 5:
        return 'closed_weekend'
    t = now.strftime('%H%M')
    if t < '0930':
        return 'pre_market'
    elif t <= '1500':
        return 'trading'
    else:
        return 'closed'


def classify_board(code):
    if isinstance(code, str):
        if code.startswith('sz30'):
            return 'gem'
        if code.startswith('sh688'):
            return 'star'
        if code.startswith('bj') or code.startswith('8') or code.startswith('4'):
            return 'bse'
        if code.startswith('sh60') or code.startswith('sz00') or code.startswith('sz001'):
            return 'main'
    return 'main'


def is_gem(code):
    return classify_board(code) == 'gem'


def is_star(code):
    return classify_board(code) == 'star'


# ==================== 板块行情数据（东方财富） ====================

EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com'
}


def get_concept_sectors(page=1, num=500):
    """
    获取东方财富概念板块行情数据
    返回: [{code, name, change_pct, up_count, down_count, lead_stock, lead_change}, ...]
    """
    url = 'https://push2.eastmoney.com/api/qt/clist/get'
    params = {
        'cb': 'jQuery',
        'pn': page,
        'pz': num,
        'po': '1',
        'np': '1',
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': '2',
        'invt': '2',
        'fid': 'f3',
        'fs': 'm:90+t:2',
        'fields': 'f2,f3,f4,f12,f14,f104,f105,f128,f140,f136',
    }
    try:
        r = requests.get(url, params=params, headers=EM_HEADERS, timeout=15)
        # 去除JSONP回调
        text = re.sub(r'^jQuery\(', '', r.text)
        text = re.sub(r'\)$', '', text)
        data = json.loads(text)
        items = data.get('data', {}).get('diff', [])
        results = []
        for item in items:
            try:
                results.append({
                    'code': item.get('f12', ''),
                    'name': item.get('f14', ''),
                    'change_pct': float(item.get('f3', 0) or 0),
                    'up_count': int(item.get('f104', 0) or 0),
                    'down_count': int(item.get('f105', 0) or 0),
                    'lead_stock': item.get('f128', ''),
                    'lead_change': float(item.get('f136', 0) or 0),
                })
            except (ValueError, TypeError):
                continue
        return results
    except Exception as e:
        print(f"板块数据获取失败: {e}")
        return []


def get_stock_industry_f10(code):
    """
    通过东方财富F10接口获取个股行业分类(EM2016格式)
    code: sh600519 或 sz000001
    返回: "食品饮料-饮料-白酒" 或 None
    """
    # 转换代码格式: sh600519 → SH600519
    em_code = code.upper()
    url = f'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={em_code}'
    try:
        r = requests.get(url, headers=EM_HEADERS, timeout=10)
        data = r.json()
        # EM2016行业分类在jbzl[0]['EM2016']中
        jbzl = data.get('jbzl', [])
        if isinstance(jbzl, list) and jbzl:
            industry = jbzl[0].get('EM2016', '')
            if industry:
                return industry
        # 尝试其他字段
        industry = data.get('sshy', '')
        if industry:
            return industry
        hy = data.get('hy', {})
        if isinstance(hy, list) and hy:
            industry = hy[0].get('EM2016', '') or hy[0].get('INDUSTRYCSRC1', '')
            if industry:
                return industry
        elif isinstance(hy, dict):
            industry = hy.get('EM2016', '') or hy.get('INDUSTRYCSRC1', '')
            if industry:
                return industry
        return None
    except Exception as e:
        return None
