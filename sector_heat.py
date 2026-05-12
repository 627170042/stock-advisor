"""
A股T+1短线选股系统 - 板块热度评估模块 v2

核心变化（v1→v2）：
1. 主数据源从东方财富push2切换到新浪行业板块（更稳定可靠）
2. 东方财富push2在沙盒/GitHub Actions均可能被墙，新浪接口更稳定
3. 行业分类仍用东方财富F10接口（emweb域名可用）
4. 匹配策略：F10行业分类 → 新浪行业板块关键词匹配 → 股票名称匹配

数据源说明：
- 新浪行业板块: https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php
  返回49个申万行业板块，含涨跌幅、领涨股等信息
- 东方财富F10: https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax
  返回个股的EM2016行业分类，如"电气设备-输变电设备-其他输变电设备"

应用场景：
- 热门板块连涨5天 = 趋势加速（减轻惩罚）
- 冷门板块连涨5天 = 动量衰减（加重惩罚）
"""
import re
import time
import json
import requests
from data_fetcher import get_stock_industry_f10

# 新浪行业板块请求头
SINA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn'
}


class SectorHeatMap:
    """板块热度数据管理 v2"""

    # EM2016行业分类关键词 → 新浪行业板块名称 映射表
    # 东方财富F10返回如"电气设备-输变电设备-其他输变电设备"
    # 新浪板块名为"电力行业""发电设备"等
    INDUSTRY_TO_SINA = {
        # EM2016一级分类 → 新浪板块名（可多个，按优先级排列）
        '电力': ['电力行业', '发电设备'],
        '电气设备': ['发电设备', '电器行业'],
        '电源设备': ['电力行业', '发电设备'],
        '食品饮料': ['酿酒行业', '食品行业'],
        '半导体': ['电子器件', '电子信息'],
        '消费电子': ['电子器件', '家电行业'],
        '汽车': ['汽车制造', '汽车整车'],
        '医药': ['医药行业', '生物制药'],
        '银行': ['银行板块'],
        '房地产': ['房地产'],
        '通信': ['通信行业', '电子信息'],
        '计算机': ['电子信息', '计算机'],
        '国防军工': ['飞机制造', '船舶制造'],
        '有色金属': ['有色金属'],
        '煤炭': ['煤炭行业', '煤化工'],
        '石油石化': ['石油行业', '化工行业'],
        '钢铁': ['钢铁行业'],
        '机械设备': ['机械行业'],
        '电子': ['电子器件', '电子信息'],
        '电子设备': ['电子器件', '电子信息'],
        '传媒': ['传媒娱乐'],
        '建筑': ['建筑建材', '水泥行业'],
        '化工': ['化工行业'],
        '纺织服饰': ['纺织行业'],
        '商贸零售': ['商业百货'],
        '农林牧渔': ['农林牧渔'],
        '公用事业': ['供水供气', '电力行业'],
        '交通运输': ['公路桥梁', '水上运输'],
        '非银金融': ['券商板块', '保险板块'],
        '家用电器': ['家电行业'],
        '轻工制造': ['印刷包装', '造纸行业'],
        '综合': ['综合行业'],
        '建筑材料': ['水泥行业', '建筑建材'],
        '美容护理': ['化妆品'],
        '环保': ['环保行业'],
        '社会服务': ['旅游行业'],
        '国防': ['飞机制造', '船舶制造'],
    }

    # 新浪板块名 → EM2016关键词 反向映射（用于无F10时的降级匹配）
    SINA_KEYWORDS = {
        '电力行业': ['电力', '绿电', '发电', '水电', '火电', '核电', '风电'],
        '发电设备': ['电气', '输变电', '配电', '逆变器', '光伏设备'],
        '电器行业': ['电器', '家电', '空调', '冰箱'],
        '电子信息': ['计算机', '通信', 'AI', '软件', '芯片', '半导体', '人工智能'],
        '电子器件': ['电子', '半导体', '芯片', '集成电路', '消费电子'],
        '机械行业': ['机械', '工程机械', '工业母机', '机器人'],
        '化工行业': ['化工', '化学', '石化', '塑料'],
        '有色金属': ['有色', '铜', '铝', '锂', '稀土', '钴'],
        '钢铁行业': ['钢铁', '特钢'],
        '煤炭行业': ['煤炭', '煤'],
        '石油行业': ['石油', '石化', '天然气'],
        '汽车制造': ['汽车', '新能源车', '整车'],
        '医药行业': ['医药', '生物', '制药'],
        '酿酒行业': ['白酒', '啤酒', '黄酒', '葡萄酒'],
        '房地产': ['房地产', '地产', '物业'],
        '建筑建材': ['建筑', '基建', '建材'],
        '水泥行业': ['水泥'],
        '农林牧渔': ['农业', '牧', '渔', '种业'],
        '飞机制造': ['航空', '飞机', '军工', '航天'],
        '船舶制造': ['船舶', '造船', '海运'],
    }

    def __init__(self):
        self.sectors_data = {}  # {板块key: {name, change_pct, lead_stock, ...}}
        self._loaded = False
        # 个股→板块映射缓存（避免重复查询F10）
        self._stock_sector_cache = {}

    def fetch_sector_heat_data(self):
        """从新浪获取行业板块热度数据（一次调用，49个行业）"""
        if self._loaded:
            return

        print("  获取板块热度数据（新浪行业板块）...")
        try:
            url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
            r = requests.get(url, headers=SINA_HEADERS, timeout=15)

            if r.status_code != 200 or len(r.text) < 100:
                print(f"  ⚠️ 新浪行业板块数据获取失败(status={r.status_code})，尝试东方财富...")
                self._try_eastmoney_fallback()
                self._loaded = True
                return

            # 解析JSONP风格数据
            match = re.search(r'\{(.+)\}', r.text, re.DOTALL)
            if not match:
                print("  ⚠️ 新浪行业板块数据格式异常，尝试东方财富...")
                self._try_eastmoney_fallback()
                self._loaded = True
                return

            raw = match.group(0)
            items = re.findall(r'"([^"]+)":"([^"]+)"', raw)

            for key, val in items:
                parts = val.split(',')
                if len(parts) < 13:
                    continue
                try:
                    sector_key = parts[0]
                    sector_name = parts[1]
                    stock_count = int(parts[2])
                    change_pct = float(parts[5])
                    lead_code = parts[8]
                    lead_name = parts[12]
                    lead_change = float(parts[11]) if parts[11] else 0

                    self.sectors_data[sector_key] = {
                        'name': sector_name,
                        'stock_count': stock_count,
                        'change_pct': change_pct,
                        'lead_code': lead_code,
                        'lead_name': lead_name,
                        'lead_change': lead_change,
                        'heat_score': self._calc_heat_score(change_pct, stock_count, lead_change),
                    }
                except (ValueError, IndexError):
                    continue

            # 按热度排序，打印top5
            sorted_sectors = sorted(self.sectors_data.values(),
                                    key=lambda x: x['heat_score'], reverse=True)
            print(f"  已加载 {len(self.sectors_data)} 个行业板块")
            for s in sorted_sectors[:5]:
                icon = '🔥' if s['heat_score'] >= 70 else ('📊' if s['heat_score'] >= 40 else '❄️')
                print(f"    {icon} {s['name']}: 热度{s['heat_score']}分 涨{s['change_pct']:.2f}% "
                      f"领涨:{s['lead_name']}")

            self._loaded = True

        except Exception as e:
            print(f"  ⚠️ 板块数据获取异常: {e}，尝试东方财富...")
            self._try_eastmoney_fallback()
            self._loaded = True

    def _try_eastmoney_fallback(self):
        """东方财富概念板块降级方案"""
        try:
            from data_fetcher import get_concept_sectors
            sectors = get_concept_sectors(page=1, num=500)
            if not sectors:
                print("  ⚠️ 东方财富板块数据也不可用，将使用默认热度")
                return

            for s in sectors:
                code = s.get('code', '')
                name = s.get('name', '')
                change_pct = s.get('change_pct', 0)
                up_count = s.get('up_count', 0)
                down_count = s.get('down_count', 0)
                lead_stock = s.get('lead_stock', '')
                lead_change = s.get('lead_change', 0)

                self.sectors_data[code] = {
                    'name': name,
                    'stock_count': up_count + down_count,
                    'change_pct': change_pct,
                    'lead_code': '',
                    'lead_name': lead_stock,
                    'lead_change': lead_change,
                    'heat_score': self._calc_heat_score(change_pct, up_count + down_count, lead_change,
                                                        has_up_down=True, up_count=up_count, down_count=down_count),
                }

            print(f"  东方财富降级: 已加载 {len(self.sectors_data)} 个概念板块")
        except Exception as e:
            print(f"  ⚠️ 东方财富降级也失败: {e}")

    def _calc_heat_score(self, change_pct, stock_count, lead_change,
                         has_up_down=False, up_count=0, down_count=0):
        """计算板块热度评分 (0-100)"""
        score = 0

        # 1. 板块涨幅 (0-40分)
        if change_pct >= 3:
            score += 40
        elif change_pct >= 2:
            score += 30
        elif change_pct >= 1:
            score += 20
        elif change_pct >= 0:
            score += 10
        else:
            # 跌幅越大越冷
            if change_pct >= -1:
                score += 5
            elif change_pct >= -2:
                score += 0
            else:
                score -= 5

        # 2. 上涨占比 (0-30分) - 新浪数据没有up/down，用涨幅和股票数估算
        if has_up_down and (up_count + down_count) > 0:
            up_ratio = up_count / (up_count + down_count)
            if up_ratio >= 0.80:
                score += 30
            elif up_ratio >= 0.60:
                score += 20
            elif up_ratio >= 0.50:
                score += 10
        else:
            # 用板块涨幅估算：涨幅>1%推断多数上涨
            if change_pct >= 1.5:
                score += 25
            elif change_pct >= 0.5:
                score += 15
            elif change_pct >= 0:
                score += 8

        # 3. 领涨股强度 (0-30分)
        if lead_change >= 8:
            score += 30
        elif lead_change >= 5:
            score += 20
        elif lead_change >= 2:
            score += 10
        elif lead_change >= 0:
            score += 3

        return max(0, min(100, score))

    def _get_heat_level(self, heat_score):
        """热度等级判定"""
        if heat_score >= 70:
            return 'hot'
        elif heat_score >= 40:
            return 'warm'
        else:
            return 'cold'

    def match_stock_to_sector(self, stock_code, stock_name):
        """
        将个股映射到最匹配的行业板块
        策略（按优先级）：
        1. 查缓存（同一股票只查一次F10）
        2. F10获取EM2016行业分类 → 映射表匹配新浪板块
        3. F10行业关键词直接匹配新浪板块名
        4. 股票名称关键词匹配（降级）
        返回: {sector_code, sector_name, heat_score, heat_level} 或 None
        """
        if not self.sectors_data:
            return None

        # 查缓存
        cache_key = f"{stock_code}_{stock_name}"
        if cache_key in self._stock_sector_cache:
            return self._stock_sector_cache[cache_key]

        result = None

        # 1. 通过F10获取行业分类
        industry = get_stock_industry_f10(stock_code)
        time.sleep(0.15)  # 限速

        if industry:
            # industry 格式: "电气设备-输变电设备-其他输变电设备"
            industry_parts = industry.split('-')

            # 策略A: 映射表匹配（从最细粒度往粗匹配）
            # 先尝试二三级分类，再尝试一级分类
            for part in reversed(industry_parts):
                if part in self.INDUSTRY_TO_SINA:
                    sina_names = self.INDUSTRY_TO_SINA[part]
                    for sina_name in sina_names:
                        for sector_key, sector_info in self.sectors_data.items():
                            if sina_name == sector_info['name']:
                                result = self._build_result(sector_key, sector_info)
                                break
                        if result:
                            break
                    if result:
                        break

            # 如果细粒度没匹配到，尝试一级分类
            if not result:
                industry_main = industry_parts[0]
                if industry_main in self.INDUSTRY_TO_SINA:
                    sina_names = self.INDUSTRY_TO_SINA[industry_main]
                    for sina_name in sina_names:
                        for sector_key, sector_info in self.sectors_data.items():
                            if sina_name == sector_info['name']:
                                result = self._build_result(sector_key, sector_info)
                                break
                        if result:
                            break

            # 策略B: 行业关键词直接匹配板块名
            if not result:
                best_match = None
                best_score = 0
                for sector_key, sector_info in self.sectors_data.items():
                    sector_name = sector_info['name']
                    match_score = 0
                    # 行业各层级关键词匹配板块名
                    for part in industry_parts:
                        if part in sector_name:
                            match_score += 10
                        elif sector_name in part:
                            match_score += 5
                    if match_score > best_score:
                        best_score = match_score
                        best_match = sector_key

                if best_match and best_score >= 5:
                    result = self._build_result(best_match, self.sectors_data[best_match])

        # 2. 股票名称关键词匹配（降级策略）
        if not result:
            best_match = None
            best_score = 0
            for sector_key, sector_info in self.sectors_data.items():
                sector_name = sector_info['name']
                keywords = self.SINA_KEYWORDS.get(sector_name, [])
                match_score = 0
                for kw in keywords:
                    if kw in stock_name:
                        match_score += 3
                # 也用板块名直接匹配
                for char in sector_name:
                    if char in stock_name and char not in '股份集团科技电子':
                        match_score += 1
                if match_score > best_score:
                    best_score = match_score
                    best_match = sector_key

            if best_match and best_score >= 3:
                result = self._build_result(best_match, self.sectors_data[best_match])

        # 缓存结果
        self._stock_sector_cache[cache_key] = result
        return result

    def _build_result(self, sector_key, sector_info):
        """构建统一的结果格式"""
        heat_score = sector_info['heat_score']
        return {
            'sector_code': sector_key,
            'sector_name': sector_info['name'],
            'heat_score': heat_score,
            'heat_level': self._get_heat_level(heat_score),
            'sector_change': sector_info['change_pct'],
            'up_ratio': 0.6 if sector_info['change_pct'] > 0 else 0.4,  # 估算值
        }


def get_sector_heat_for_stock(stock_code, stock_name, sector_map=None):
    """对外统一接口：获取某只股票所属板块的热度"""
    if sector_map is None or not sector_map._loaded:
        return None

    result = sector_map.match_stock_to_sector(stock_code, stock_name)
    if result is None:
        # 无匹配时返回温热默认值
        return {
            'sector_code': '',
            'sector_name': '未知',
            'heat_score': 50,
            'heat_level': 'warm',
            'sector_change': 0,
            'up_ratio': 0.5,
        }
    return result
