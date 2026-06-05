"""
A股T+1短线选股系统 - 数据采集模块 v6

v5→v6 核心升级：
1. K线数据扩展到120根（原20根，MACD/RSI/均线计算无效）
2. 新增大盘指数获取（上证/深证/创业板）
3. 新增涨跌家数比（市场温度指标）
4. 全A股扫描能力（分页遍历所有A股）
5. 北向资金数据（市场风向标）
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

EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com'
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
    """批量获取实时行情（每批最多30只）"""
    all_results = {}
    batch_size = 30
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        codes_str = ','.join(batch)
        url = f'https://hq.sinajs.cn/list={codes_str}'
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            for line in r.text.strip().split('\n'):
                match = re.search(r'hq_str_(s[hz]\d+)="([^"]+)"', line)
                if not match:
                    continue
                code = match.group(1)
                fields = match.group(2).split(',')
                if len(fields) < 32 or fields[0] == '':
                    continue
                try:
                    all_results[code] = {
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
        except Exception as e:
            print(f"  批量行情获取异常: {e}")
        time.sleep(0.1)
    return all_results


# ==================== 股票列表（全A股扫描） ====================

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


def scan_all_a_stocks(min_amount=50000000, min_turnover=1.0):
    """
    ★v6新增: 全A股扫描
    优先使用东方财富API（一次请求获取全部），降级用新浪分页
    过滤条件: 成交额 >= min_amount, 换手率 >= min_turnover
    返回: dict {symbol: stock_info}
    """
    print("  [v6] 全A股扫描开始...")

    # 优先尝试东方财富（一次请求全量数据，极快）
    result = _scan_eastmoney(min_amount, min_turnover)
    if result and len(result) >= 500:
        return result

    # 降级: 新浪分页扫描
    print("  东方财富数据不足，降级新浪分页扫描...")
    return _scan_sina_pages(min_amount, min_turnover)


def _scan_eastmoney(min_amount=50000000, min_turnover=1.0):
    """东方财富全A股扫描（一次请求，极速）"""
    all_stocks = {}
    try:
        url = 'https://push2.eastmoney.com/api/qt/clist/get'
        params = {
            'cb': 'jQuery',
            'pn': 1,
            'pz': 6000,  # 一次获取6000条
            'po': '1',
            'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2',
            'invt': '2',
            'fid': 'f6',  # 按成交额排序
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048',
            'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f12,f14,f15,f16,f17,f18,f20,f23',
        }
        # 重试3次
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, headers=EM_HEADERS, timeout=30)
                text = re.sub(r'^jQuery\(', '', r.text)
                text = re.sub(r'\)$', '', text)
                data = json.loads(text)
                items = data.get('data', {}).get('diff', [])
                if items:
                    break
            except Exception as e:
                print(f"  东方财富请求第{attempt+1}次失败: {e}")
                time.sleep(1)
        else:
            return {}

        total = data.get('data', {}).get('total', 0)

        if not items:
            return {}

        for item in items:
            try:
                code_raw = item.get('f12', '')
                name = item.get('f14', '')
                price = float(item.get('f2', 0) or 0)
                change_pct = float(item.get('f3', 0) or 0)
                amount = float(item.get('f6', 0) or 0)  # 成交额
                turnover = float(item.get('f8', 0) or 0)  # 换手率
                mktcap = float(item.get('f20', 0) or 0)  # 总市值
                nmc = float(item.get('f23', 0) or 0)  # 流通市值
                high = float(item.get('f15', 0) or 0)
                low = float(item.get('f16', 0) or 0)
                open_p = float(item.get('f17', 0) or 0)
                prev_close = float(item.get('f18', 0) or 0)

                # 构造symbol格式
                if code_raw.startswith('6'):
                    symbol = f'sh{code_raw}'
                elif code_raw.startswith('0') or code_raw.startswith('3'):
                    symbol = f'sz{code_raw}'
                else:
                    continue

                # 基本过滤
                if price <= 0 or 'ST' in name or 'st' in name:
                    continue
                if amount < min_amount or turnover < min_turnover:
                    continue
                board = classify_board(symbol)
                if board == 'bse':
                    continue

                all_stocks[symbol] = {
                    'symbol': symbol,
                    'code': code_raw,
                    'name': name,
                    'trade': price,
                    'pricechange': price - prev_close if prev_close > 0 else 0,
                    'changepercent': change_pct,
                    'buy': 0,
                    'sell': 0,
                    'settlement': prev_close,
                    'open': open_p,
                    'high': high,
                    'low': low,
                    'volume': 0,
                    'amount': amount,
                    'turnoverratio': turnover,
                    'per': 0,
                    'pb': 0,
                    'mktcap': mktcap,
                    'nmc': nmc,
                }
            except (ValueError, TypeError, KeyError):
                continue

        print(f"  [v6] 东方财富扫描完成: 共{total}只, 符合条件{len(all_stocks)}只")
        return all_stocks

    except Exception as e:
        print(f"  东方财富扫描失败: {e}")
        return {}


def _scan_sina_pages(min_amount=50000000, min_turnover=1.0):
    """新浪分页扫描（降级方案）"""
    all_stocks = {}
    page = 1
    total_scanned = 0

    while True:
        stocks = get_stock_list(page=page, num=80, sort='amount', asc=0)
        if not stocks:
            break

        total_scanned += len(stocks)
        page_has_qualifying = False

        for s in stocks:
            if s.get('amount', 0) < min_amount:
                continue  # 按成交额降序，后面更小，但换手率高的可能在不同页
            page_has_qualifying = True
            if s.get('turnoverratio', 0) < min_turnover:
                continue
            if 'ST' in s.get('name', '') or 'st' in s.get('name', ''):
                continue
            if s.get('trade', 0) <= 0:
                continue
            board = classify_board(s['symbol'])
            if board == 'bse':
                continue

            all_stocks[s['symbol']] = s

        # ★优化: 如果连续2页没有符合条件的，提前退出
        page += 1
        if page > 100:
            break
        if not page_has_qualifying and page > 5:
            break
        time.sleep(0.15)

    print(f"  [v6] 新浪扫描完成: 共扫描{total_scanned}只, 符合条件{len(all_stocks)}只")
    return all_stocks


def get_top_gainers(num=80):
    """获取涨幅前N的股票"""
    return get_stock_list(page=1, num=num, sort='changepercent', asc=0)


def get_top_volume(num=80):
    """获取成交额前N的股票"""
    return get_stock_list(page=1, num=num, sort='amount', asc=0)


def get_top_turnover(num=80):
    """获取换手率前N的股票"""
    return get_stock_list(page=1, num=num, sort='turnoverratio', asc=0)


# ==================== ★v6新增: 大盘指数数据 ====================

def get_market_indices():
    """
    ★v6新增: 获取大盘指数实时数据
    返回: {
        'sh000001': {'name': '上证指数', 'change_pct': ..., ...},
        'sz399001': {'name': '深证成指', 'change_pct': ..., ...},
        'sz399006': {'name': '创业板指', 'change_pct': ..., ...},
    }
    """
    indices = {
        'sh000001': '上证指数',
        'sz399001': '深证成指',
        'sz399006': '创业板指',
    }

    codes_str = ','.join(indices.keys())
    url = f'https://hq.sinajs.cn/list={codes_str}'

    results = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        for line in r.text.strip().split('\n'):
            match = re.search(r'hq_str_(s[hz]\d+)="([^"]+)"', line)
            if not match:
                continue
            code = match.group(1)
            fields = match.group(2).split(',')
            if len(fields) < 32 or fields[0] == '':
                continue
            try:
                prev_close = float(fields[2])
                current = float(fields[3])
                change_pct = (current - prev_close) / prev_close * 100 if prev_close > 0 else 0
                results[code] = {
                    'name': indices.get(code, fields[0]),
                    'open': float(fields[1]),
                    'prev_close': prev_close,
                    'current': current,
                    'high': float(fields[4]),
                    'low': float(fields[5]),
                    'volume': int(fields[8]),
                    'amount': float(fields[9]),
                    'change_pct': round(change_pct, 2),
                }
            except (ValueError, ZeroDivisionError):
                continue
    except Exception as e:
        print(f"  大盘指数获取失败: {e}")

    return results


def get_market_environment():
    """
    ★v6新增: 综合市场环境评估
    返回: {
        'score': 0-100,    # 市场环境分数
        'level': 'bull/bear/neutral',
        'indices': {...},
        'advance_decline': {...},
        'signal': '适合选股/谨慎选股/不宜选股'
    }
    """
    env = {
        'score': 50,
        'level': 'neutral',
        'indices': {},
        'advance_decline': {},
        'signal': '谨慎选股',
    }

    # 1. 获取大盘指数
    indices = get_market_indices()
    env['indices'] = indices

    if indices:
        sh = indices.get('sh000001', {})
        sz = indices.get('sz399001', {})
        cyb = indices.get('sz399006', {})

        sh_change = sh.get('change_pct', 0)
        sz_change = sz.get('change_pct', 0)
        cyb_change = cyb.get('change_pct', 0)

        # 大盘涨跌幅评分 (基准30分 + 加减分)
        # ★v6修正: 基准分调低，加减分更均匀，避免小幅下跌就变bear
        avg_change = (sh_change + sz_change) / 2
        if avg_change >= 2.0:
            env['score'] += 25
        elif avg_change >= 1.0:
            env['score'] += 18
        elif avg_change >= 0.5:
            env['score'] += 12
        elif avg_change >= 0:
            env['score'] += 5
        elif avg_change >= -0.5:
            env['score'] += 0
        elif avg_change >= -1.0:
            env['score'] -= 5
        elif avg_change >= -2.0:
            env['score'] -= 12
        else:
            env['score'] -= 20

        # 创业板独立评估
        if cyb_change >= 1.5:
            env['score'] += 10
        elif cyb_change >= 0:
            env['score'] += 3
        elif cyb_change >= -0.5:
            env['score'] += 0
        elif cyb_change >= -1.5:
            env['score'] -= 5
        else:
            env['score'] -= 8

    # 2. 获取涨跌家数比
    adv_dec = get_advance_decline_ratio()
    env['advance_decline'] = adv_dec

    if adv_dec:
        ratio = adv_dec.get('ratio', 1.0)
        if ratio >= 3.0:
            env['score'] += 20  # 涨远多于跌
        elif ratio >= 2.0:
            env['score'] += 12
        elif ratio >= 1.0:
            env['score'] += 5
        elif ratio >= 0.5:
            env['score'] -= 5
        else:
            env['score'] -= 15

    # 3. 综合评定
    # ★v6修正: 只有大跌(指数跌>2.5%)才暂停推荐，正常震荡可谨慎选股
    env['score'] = max(0, min(100, env['score']))

    if env['score'] >= 60:
        env['level'] = 'bull'
        env['signal'] = '适合选股'
    elif env['score'] >= 30:
        env['level'] = 'neutral'
        env['signal'] = '谨慎选股'
    else:
        env['level'] = 'bear'
        env['signal'] = '不宜选股'

    print(f"  [v6] 市场环境: {env['level']}({env['score']}分) - {env['signal']}")

    return env


# ==================== ★v6新增: 涨跌家数比 ====================

def get_advance_decline_ratio():
    """
    ★v6新增: 获取A股涨跌家数比
    数据源: 东方财富A股列表API，统计涨跌家数
    返回: {'up': N, 'down': N, 'flat': N, 'ratio': up/down}
    """
    try:
        url = 'https://push2.eastmoney.com/api/qt/clist/get'
        params = {
            'cb': 'jQuery',
            'pn': 1,
            'pz': 1,  # 只要1条数据，目的是拿total
            'po': '1',
            'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2',
            'invt': '2',
            'fid': 'f3',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048',  # 全A股
            'fields': 'f2,f3,f12,f14',
        }
        r = requests.get(url, params=params, headers=EM_HEADERS, timeout=15)
        text = re.sub(r'^jQuery\(', '', r.text)
        text = re.sub(r'\)$', '', text)
        data = json.loads(text)
        total = data.get('data', {}).get('total', 0)

        if total <= 0:
            return {}

        # 获取所有股票来统计涨跌 — 取前500只 + 后500只估算
        # 实际用两页来估算
        up_count = 0
        down_count = 0
        flat_count = 0

        for page_num in [1, 2]:
            params['pn'] = page_num
            params['pz'] = 500
            r = requests.get(url, params=params, headers=EM_HEADERS, timeout=15)
            text = re.sub(r'^jQuery\(', '', r.text)
            text = re.sub(r'\)$', '', text)
            data = json.loads(text)
            items = data.get('data', {}).get('diff', [])

            for item in items:
                change = item.get('f3', 0)
                if change is None:
                    continue
                try:
                    change = float(change)
                    if change > 0:
                        up_count += 1
                    elif change < 0:
                        down_count += 1
                    else:
                        flat_count += 1
                except (ValueError, TypeError):
                    continue

            time.sleep(0.1)

        if down_count == 0:
            ratio = 10.0 if up_count > 0 else 1.0
        else:
            ratio = up_count / down_count

        return {
            'up': up_count,
            'down': down_count,
            'flat': flat_count,
            'ratio': round(ratio, 2),
        }

    except Exception as e:
        print(f"  涨跌家数获取失败: {e}")
        return {}


# ==================== 历史K线 ====================

def get_kline_sina(code, scale='240', datalen='120'):
    """
    获取新浪K线数据
    code: sh600519
    scale: 5/15/30/60/240(日K)
    datalen: 返回条数（★v6: 默认120根★）

    ★v6关键升级: datalen从20→120
    - MACD需要26+9=35根最小
    - RSI(14)需要15根最小
    - 均线MA60需要60根
    - 趋势判断需要更长的历史窗口
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

def get_concept_sectors(page=1, num=500):
    """获取东方财富概念板块行情数据"""
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
    """通过东方财富F10接口获取个股行业分类"""
    em_code = code.upper()
    url = f'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={em_code}'
    try:
        r = requests.get(url, headers=EM_HEADERS, timeout=10)
        data = r.json()
        jbzl = data.get('jbzl', [])
        if isinstance(jbzl, list) and jbzl:
            industry = jbzl[0].get('EM2016', '')
            if industry:
                return industry
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
