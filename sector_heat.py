"""
A股T+1短线选股系统 - 板块热度评估模块

核心能力：
1. 从东方财富获取概念板块行情数据（一次调用，496个板块）
2. 将个股映射到最匹配的概念板块
3. 计算板块热度评分（0-100分）
4. 区分 hot/warm/cold 三个热度等级

应用场景：
- 热门板块连涨5天 = 趋势加速（减轻惩罚）
- 冷门板块连涨5天 = 动量衰减（加重惩罚）
"""
import re
import time
import json
import requests
from data_fetcher import get_concept_sectors, get_stock_industry_f10


class SectorHeatMap:
    """板块热度数据管理"""

    # 行业关键词 → 概念板块关键词 映射表
    # EM2016行业分类到概念板块的桥梁
    INDUSTRY_KEYWORDS = {
        '电力': ['电力', '绿电', '新能源', '风电', '光伏', '核电'],
        '半导体': ['半导体', '芯片', '集成电路', '封测', '光刻'],
        '消费电子': ['消费电子', '苹果', '华为', '小米', 'VR', 'AR'],
        '汽车': ['汽车', '新能源车', '锂电池', '充电桩', '智能驾驶'],
        '医药': ['医药', '生物', '中药', '医疗器械', 'CXO'],
        '食品饮料': ['食品', '白酒', '饮料', '乳业', '调味品'],
        '银行': ['银行', '金融', '券商', '保险'],
        '房地产': ['房地产', '地产', '物业'],
        '通信': ['通信', '5G', '6G', '光通信', '算力'],
        '计算机': ['计算机', 'AI', '人工智能', '大数据', '云计算', '信创'],
        '国防军工': ['军工', '航天', '航空', '兵器', '舰船'],
        '有色金属': ['有色', '铜', '铝', '锂', '稀土', '钴', '镍'],
        '煤炭': ['煤炭', '煤化工'],
        '石油石化': ['石油', '石化', '化工', '天然气'],
        '钢铁': ['钢铁', '特钢'],
        '机械设备': ['机械', '工程机械', '工业母机', '机器人'],
        '电力设备': ['光伏', '风电', '储能', '逆变器', '新能源'],
        '电子': ['电子', '面板', 'LED', 'MLCC', '被动元件'],
        '传媒': ['传媒', '游戏', '影视', '广告', '直播'],
        '建筑': ['建筑', '基建', '建材', '水泥'],
    }

    def __init__(self):
        self.sectors_data = {}  # {板块代码: {name, change_pct, up_count, down_count, ...}}
        self._loaded = False

    def fetch_sector_heat_data(self):
        """从东方财富获取概念板块热度数据（一次调用）"""
        if self._loaded:
            return

        print("  获取板块热度数据...")
        try:
            sectors = get_concept_sectors(page=1, num=500)
            if not sectors:
                print("  ⚠️ 板块数据获取失败，将使用默认热度")
                self._loaded = True
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
                    'change_pct': change_pct,
                    'up_count': up_count,
                    'down_count': down_count,
                    'lead_stock': lead_stock,
                    'lead_change': lead_change,
                    'heat_score': self._calc_heat_score(change_pct, up_count, down_count, lead_change),
                }

            # 按热度排序，打印top5
            sorted_sectors = sorted(self.sectors_data.values(), key=lambda x: x['heat_score'], reverse=True)
            print(f"  已加载 {len(self.sectors_data)} 个板块")
            for i, s in enumerate(sorted_sectors[:5]):
                level = '🔥' if s['heat_score'] >= 70 else '📊'
                print(f"    {level} {s['name']}: 热度{s['heat_score']}分 涨{s['change_pct']:.1f}% "
                      f"上涨{s['up_count']}/下跌{s['down_count']}")

            self._loaded = True
        except Exception as e:
            print(f"  ⚠️ 板块数据获取异常: {e}")
            self._loaded = True

    def _calc_heat_score(self, change_pct, up_count, down_count, lead_change):
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

        # 2. 上涨占比 (0-30分)
        total = up_count + down_count
        if total > 0:
            up_ratio = up_count / total
            if up_ratio >= 0.80:
                score += 30
            elif up_ratio >= 0.60:
                score += 20
            elif up_ratio >= 0.50:
                score += 10

        # 3. 领涨股强度 (0-30分)
        if lead_change >= 8:
            score += 30
        elif lead_change >= 5:
            score += 20
        elif lead_change >= 2:
            score += 10

        return score

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
        将个股映射到最匹配的概念板块
        策略：
        1. F10获取行业分类
        2. 行业关键词匹配概念板块
        3. 股票名称关键词匹配
        返回: {sector_code, sector_name, heat_score, heat_level} 或 None
        """
        if not self.sectors_data:
            return None

        # 1. 通过F10获取行业分类
        industry = get_stock_industry_f10(stock_code)
        time.sleep(0.2)  # 限速

        # 2. 行业关键词匹配
        if industry:
            # industry 格式: "电力设备-光伏设备-逆变器"
            industry_parts = industry.split('-')
            best_match = None
            best_score = 0

            for sector_code, sector_info in self.sectors_data.items():
                match_score = 0
                sector_name = sector_info['name']

                # 行业关键词匹配
                for part in industry_parts:
                    if part in sector_name or sector_name in part:
                        match_score += 10

                # 额外通过映射表匹配
                for ind_key, keywords in self.INDUSTRY_KEYWORDS.items():
                    if any(kw in industry for kw in [ind_key] + keywords[:1]):
                        if any(kw in sector_name for kw in keywords):
                            match_score += 5

                if match_score > best_score:
                    best_score = match_score
                    best_match = sector_code

            if best_match and best_score >= 5:
                sector_info = self.sectors_data[best_match]
                return {
                    'sector_code': best_match,
                    'sector_name': sector_info['name'],
                    'heat_score': sector_info['heat_score'],
                    'heat_level': self._get_heat_level(sector_info['heat_score']),
                    'sector_change': sector_info['change_pct'],
                    'up_ratio': sector_info['up_count'] / max(1, sector_info['up_count'] + sector_info['down_count']),
                }

        # 3. 股票名称关键词匹配（降级策略）
        best_match = None
        best_score = 0
        for sector_code, sector_info in self.sectors_data.items():
            sector_name = sector_info['name']
            match_score = 0
            # 板块名称中的关键词出现在股票名称中
            for char in sector_name:
                if char in stock_name and char not in '股份集团科技电子':
                    match_score += 1
            if match_score > best_score:
                best_score = match_score
                best_match = sector_code

        if best_match and best_score >= 2:
            sector_info = self.sectors_data[best_match]
            return {
                'sector_code': best_match,
                'sector_name': sector_info['name'],
                'heat_score': sector_info['heat_score'],
                'heat_level': self._get_heat_level(sector_info['heat_score']),
                'sector_change': sector_info['change_pct'],
                'up_ratio': sector_info['up_count'] / max(1, sector_info['up_count'] + sector_info['down_count']),
            }

        return None


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
