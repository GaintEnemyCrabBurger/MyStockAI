"""
core/search.py — 跨市场股票模糊搜索（A股 / 港股 / 美股）

设计取舍
--------
- 全市场列表通过 akshare 在线拉取代价高（美股需遍历 131+ 页，
  A/HK 亦需一次性下载数千条），不适合每次输入都刷新。
- 改用 **手工维护的精选清单**：每个市场覆盖 40-120 只高关注度标的，
  附中英文双名与板块标签，支持中文/英文/拼音缩写/ticker 前缀模糊搜索。
- 表外任意合法代码仍可由用户手动键入（UI 层用 multiselect
  的 `accept_new_options`），`config.detect_and_normalize` 负责规范化。

对外接口
--------
search_all(keyword, limit=20, markets=None)
    跨市场模糊搜索，按命中精度排序
    返回 [{market, code, name_en, name_zh, sector, label}, ...]

build_catalog(markets=None)
    构建完整目录，供 UI 的 multiselect 作为 options 使用
    返回 [{market, code, label}, ...]（label = "CODE · 中文名 · 市场"）
"""

from __future__ import annotations

from typing import List, Dict, Optional, Sequence

# ---------------------------------------------------------------------------
# 美股精选（~120 只，涵盖七雄、半导体、中概、ETF 等）
# 字段：ticker, 英文名, 中文名, 板块
# ---------------------------------------------------------------------------

US_POPULAR_STOCKS: list[tuple[str, str, str, str]] = [
    # ==== 科技巨头 ====
    ("AAPL",  "Apple Inc.",                 "苹果",          "科技"),
    ("MSFT",  "Microsoft Corporation",      "微软",          "科技"),
    ("GOOGL", "Alphabet Inc. Class A",      "谷歌 A",        "科技"),
    ("GOOG",  "Alphabet Inc. Class C",      "谷歌 C",        "科技"),
    ("AMZN",  "Amazon.com Inc.",            "亚马逊",        "消费"),
    ("META",  "Meta Platforms Inc.",        "Meta 脸书",     "科技"),
    ("NVDA",  "NVIDIA Corporation",         "英伟达",        "半导体"),
    ("TSLA",  "Tesla Inc.",                 "特斯拉",        "汽车"),
    ("NFLX",  "Netflix Inc.",               "奈飞",          "传媒"),
    ("ORCL",  "Oracle Corporation",         "甲骨文",        "科技"),
    ("ADBE",  "Adobe Inc.",                 "奥多比",        "软件"),
    ("CRM",   "Salesforce Inc.",            "赛富时",        "软件"),
    ("AVGO",  "Broadcom Inc.",              "博通",          "半导体"),
    ("CSCO",  "Cisco Systems Inc.",         "思科",          "科技"),
    ("IBM",   "IBM Corporation",            "国际商业机器",  "科技"),
    ("INTC",  "Intel Corporation",          "英特尔",        "半导体"),
    ("AMD",   "Advanced Micro Devices",     "超威半导体",    "半导体"),
    ("QCOM",  "QUALCOMM Incorporated",      "高通",          "半导体"),
    ("TXN",   "Texas Instruments",          "德州仪器",      "半导体"),
    ("MU",    "Micron Technology",          "美光科技",      "半导体"),
    ("TSM",   "Taiwan Semiconductor",       "台积电",        "半导体"),
    ("ASML",  "ASML Holding N.V.",          "阿斯麦",        "半导体"),
    ("ARM",   "Arm Holdings plc",           "ARM",           "半导体"),
    ("SMCI",  "Super Micro Computer",       "超微电脑",      "半导体"),

    # ==== 软件 / 云计算 ====
    ("NOW",   "ServiceNow Inc.",            "瑟维思纳尔",    "软件"),
    ("PLTR",  "Palantir Technologies",      "帕兰提尔",      "软件"),
    ("SNOW",  "Snowflake Inc.",             "雪花",          "软件"),
    ("DDOG",  "Datadog Inc.",               "Datadog",       "软件"),
    ("CRWD",  "CrowdStrike Holdings",       "众击",          "网络安全"),
    ("NET",   "Cloudflare Inc.",            "CloudFlare",    "软件"),
    ("SHOP",  "Shopify Inc.",               "Shopify",       "软件"),
    ("SQ",    "Block Inc.",                 "Block Square",  "支付"),
    ("PYPL",  "PayPal Holdings",            "贝宝",          "支付"),
    ("UBER",  "Uber Technologies",          "优步",          "出行"),
    ("LYFT",  "Lyft Inc.",                  "Lyft",          "出行"),
    ("ABNB",  "Airbnb Inc.",                "爱彼迎",        "旅游"),
    ("SPOT",  "Spotify Technology",         "声田",          "传媒"),
    ("ZM",    "Zoom Video Communications",  "Zoom",          "软件"),
    ("DOCU",  "DocuSign Inc.",              "DocuSign",      "软件"),
    ("TEAM",  "Atlassian Corporation",      "阿特拉斯",      "软件"),
    ("INTU",  "Intuit Inc.",                "Intuit",        "软件"),

    # ==== 中概股 ====
    ("BABA",  "Alibaba Group",              "阿里巴巴",      "中概"),
    ("JD",    "JD.com Inc.",                "京东",          "中概"),
    ("PDD",   "PDD Holdings",               "拼多多",        "中概"),
    ("BIDU",  "Baidu Inc.",                 "百度",          "中概"),
    ("NIO",   "NIO Inc.",                   "蔚来",          "中概"),
    ("XPEV",  "XPeng Inc.",                 "小鹏汽车",      "中概"),
    ("LI",    "Li Auto Inc.",               "理想汽车",      "中概"),
    ("TME",   "Tencent Music",              "腾讯音乐",      "中概"),
    ("NTES",  "NetEase Inc.",               "网易",          "中概"),
    ("BILI",  "Bilibili Inc.",              "哔哩哔哩",      "中概"),
    ("TCOM",  "Trip.com Group",             "携程",          "中概"),
    ("BEKE",  "KE Holdings Inc.",           "贝壳",          "中概"),

    # ==== 金融 ====
    ("BRK.A", "Berkshire Hathaway A",       "伯克希尔 A",    "金融"),
    ("BRK.B", "Berkshire Hathaway B",       "伯克希尔 B",    "金融"),
    ("JPM",   "JPMorgan Chase",             "摩根大通",      "金融"),
    ("BAC",   "Bank of America",            "美国银行",      "金融"),
    ("WFC",   "Wells Fargo",                "富国银行",      "金融"),
    ("C",     "Citigroup Inc.",             "花旗",          "金融"),
    ("GS",    "Goldman Sachs",              "高盛",          "金融"),
    ("MS",    "Morgan Stanley",             "摩根士丹利",    "金融"),
    ("V",     "Visa Inc.",                  "Visa",          "支付"),
    ("MA",    "Mastercard Inc.",            "万事达",        "支付"),
    ("AXP",   "American Express",           "美国运通",      "金融"),
    ("SCHW",  "Charles Schwab",             "嘉信理财",      "金融"),
    ("BLK",   "BlackRock Inc.",             "贝莱德",        "金融"),

    # ==== 消费 ====
    ("WMT",   "Walmart Inc.",               "沃尔玛",        "零售"),
    ("COST",  "Costco Wholesale",           "好市多",        "零售"),
    ("HD",    "Home Depot",                 "家得宝",        "零售"),
    ("LOW",   "Lowe's Companies",           "劳氏",          "零售"),
    ("TGT",   "Target Corporation",         "塔吉特",        "零售"),
    ("NKE",   "Nike Inc.",                  "耐克",          "消费"),
    ("MCD",   "McDonald's Corporation",     "麦当劳",        "餐饮"),
    ("SBUX",  "Starbucks Corporation",      "星巴克",        "餐饮"),
    ("KO",    "Coca-Cola Company",          "可口可乐",      "食品饮料"),
    ("PEP",   "PepsiCo Inc.",               "百事",          "食品饮料"),
    ("PG",    "Procter & Gamble",           "宝洁",          "日用"),
    ("DIS",   "Walt Disney Company",        "迪士尼",        "传媒"),
    ("LULU",  "Lululemon Athletica",        "露露柠檬",      "消费"),
    ("CROX",  "Crocs Inc.",                 "卡骆驰",        "消费"),
    ("YUM",   "Yum! Brands",                "百胜餐饮",      "餐饮"),
    ("CMG",   "Chipotle Mexican Grill",     "墨式烧烤",      "餐饮"),

    # ==== 生物医药 ====
    ("JNJ",   "Johnson & Johnson",          "强生",          "医药"),
    ("PFE",   "Pfizer Inc.",                "辉瑞",          "医药"),
    ("LLY",   "Eli Lilly and Company",      "礼来",          "医药"),
    ("MRK",   "Merck & Co.",                "默克",          "医药"),
    ("ABBV",  "AbbVie Inc.",                "艾伯维",        "医药"),
    ("UNH",   "UnitedHealth Group",         "联合健康",      "医保"),
    ("NVO",   "Novo Nordisk",               "诺和诺德",      "医药"),
    ("TMO",   "Thermo Fisher Scientific",   "赛默飞",        "医药"),
    ("ABT",   "Abbott Laboratories",        "雅培",          "医药"),
    ("AMGN",  "Amgen Inc.",                 "安进",          "医药"),
    ("GILD",  "Gilead Sciences",            "吉利德",        "医药"),
    ("MRNA",  "Moderna Inc.",               "莫德纳",        "医药"),

    # ==== 能源 / 工业 ====
    ("XOM",   "Exxon Mobil Corporation",    "埃克森美孚",    "能源"),
    ("CVX",   "Chevron Corporation",        "雪佛龙",        "能源"),
    ("COP",   "ConocoPhillips",             "康菲石油",      "能源"),
    ("OXY",   "Occidental Petroleum",       "西方石油",      "能源"),
    ("BA",    "Boeing Company",             "波音",          "工业"),
    ("CAT",   "Caterpillar Inc.",           "卡特彼勒",      "工业"),
    ("GE",    "General Electric",           "通用电气",      "工业"),
    ("HON",   "Honeywell International",    "霍尼韦尔",      "工业"),
    ("DE",    "Deere & Company",            "迪尔",          "工业"),
    ("LMT",   "Lockheed Martin",            "洛克希德马丁",  "国防"),
    ("RTX",   "RTX Corporation",            "雷神",          "国防"),
    ("UPS",   "United Parcel Service",      "联合包裹",      "物流"),
    ("FDX",   "FedEx Corporation",          "联邦快递",      "物流"),

    # ==== 通信 ====
    ("T",     "AT&T Inc.",                  "AT&T",          "电信"),
    ("VZ",    "Verizon Communications",     "威瑞森",        "电信"),
    ("TMUS",  "T-Mobile US",                "T-Mobile",      "电信"),

    # ==== ETF ====
    ("SPY",   "SPDR S&P 500 ETF",           "标普 500 ETF",   "ETF"),
    ("QQQ",   "Invesco QQQ Trust",          "纳指 100 ETF",   "ETF"),
    ("IWM",   "iShares Russell 2000 ETF",   "罗素 2000 ETF",  "ETF"),
    ("DIA",   "SPDR Dow Jones ETF",         "道琼斯 ETF",     "ETF"),
    ("VOO",   "Vanguard S&P 500 ETF",       "先锋 S&P500 ETF", "ETF"),
    ("VTI",   "Vanguard Total Stock",       "先锋全市场 ETF", "ETF"),
    ("ARKK",  "ARK Innovation ETF",         "ARK 创新 ETF",   "ETF"),
    ("SOXX",  "iShares Semiconductor ETF",  "半导体 ETF",     "ETF"),
    ("XLF",   "Financial Select Sector",    "金融板块 ETF",   "ETF"),
    ("XLE",   "Energy Select Sector",       "能源板块 ETF",   "ETF"),
    ("XLK",   "Technology Select Sector",   "科技板块 ETF",   "ETF"),
    ("TQQQ",  "ProShares UltraPro QQQ",     "纳指 3 倍做多",   "ETF"),
    ("SQQQ",  "ProShares UltraPro Short QQQ", "纳指 3 倍做空",  "ETF"),
    ("GLD",   "SPDR Gold Shares",           "黄金 ETF",       "ETF"),
]

# ---------------------------------------------------------------------------
# A 股精选（~110 只，覆盖沪深两市高市值 / 高流动性 / 高讨论度标的）
# 字段：6 位代码, 英文名, 中文名, 板块
# ---------------------------------------------------------------------------

A_POPULAR_STOCKS: list[tuple[str, str, str, str]] = [
    # ==== 消费 / 食品饮料 / 白酒 ====
    ("600519", "Kweichow Moutai",           "贵州茅台",      "白酒"),
    ("000858", "Wuliangye",                 "五粮液",        "白酒"),
    ("000568", "Luzhou Laojiao",            "泸州老窖",      "白酒"),
    ("600809", "Shanxi Xinghuacun Fenjiu",  "山西汾酒",      "白酒"),
    ("000596", "Anhui Gujing Distillery",   "古井贡酒",      "白酒"),
    ("002304", "Jiangsu Yanghe Brewery",    "洋河股份",      "白酒"),
    ("600887", "Inner Mongolia Yili",       "伊利股份",      "食品饮料"),
    ("603288", "Foshan Haitian",            "海天味业",      "食品饮料"),
    ("600600", "Tsingtao Brewery",          "青岛啤酒",      "食品饮料"),
    ("000895", "Shuanghui Development",     "双汇发展",      "食品饮料"),
    ("300999", "Yihai Kerry Arawana",       "金龙鱼",        "食品饮料"),
    ("603605", "Proya Cosmetics",           "珀莱雅",        "化妆品"),
    ("300957", "Yunnan Botanee",            "贝泰妮",        "化妆品"),
    ("002352", "SF Holding",                "顺丰控股",      "物流"),

    # ==== 金融 / 银行 / 保险 / 券商 ====
    ("601398", "ICBC",                      "工商银行",      "银行"),
    ("601939", "China Construction Bank",   "建设银行",      "银行"),
    ("601288", "Agricultural Bank of China","农业银行",      "银行"),
    ("601988", "Bank of China",             "中国银行",      "银行"),
    ("600036", "China Merchants Bank",      "招商银行",      "银行"),
    ("601328", "Bank of Communications",    "交通银行",      "银行"),
    ("601166", "Industrial Bank",           "兴业银行",      "银行"),
    ("600000", "SPD Bank",                  "浦发银行",      "银行"),
    ("600016", "China Minsheng Bank",       "民生银行",      "银行"),
    ("601998", "China Citic Bank",          "中信银行",      "银行"),
    ("601818", "China Everbright Bank",     "光大银行",      "银行"),
    ("601169", "Bank of Beijing",           "北京银行",      "银行"),
    ("002142", "Bank of Ningbo",            "宁波银行",      "银行"),
    ("600919", "Bank of Jiangsu",           "江苏银行",      "银行"),
    ("000001", "Ping An Bank",              "平安银行",      "银行"),
    ("601318", "Ping An Insurance",         "中国平安",      "保险"),
    ("601628", "China Life Insurance",      "中国人寿",      "保险"),
    ("601601", "CPIC",                      "中国太保",      "保险"),
    ("601336", "New China Life Insurance",  "新华保险",      "保险"),
    ("600030", "CITIC Securities",          "中信证券",      "券商"),
    ("600837", "Haitong Securities",        "海通证券",      "券商"),
    ("000166", "Shenwan Hongyuan",          "申万宏源",      "券商"),
    ("000776", "GF Securities",             "广发证券",      "券商"),
    ("601788", "Everbright Securities",     "光大证券",      "券商"),
    ("601377", "Industrial Securities",     "兴业证券",      "券商"),

    # ==== 新能源 / 汽车 / 光伏 / 锂电 ====
    ("300750", "CATL",                      "宁德时代",      "新能源"),
    ("002594", "BYD Company",               "比亚迪",        "汽车"),
    ("601127", "Seres Group",               "赛力斯",        "汽车"),
    ("600104", "SAIC Motor",                "上汽集团",      "汽车"),
    ("000625", "Changan Automobile",        "长安汽车",      "汽车"),
    ("601633", "Great Wall Motor",          "长城汽车",      "汽车"),
    ("601238", "GAC Group",                 "广汽集团",      "汽车"),
    ("600066", "Yutong Bus",                "宇通客车",      "汽车"),
    ("600660", "Fuyao Glass",               "福耀玻璃",      "汽车"),
    ("002460", "Ganfeng Lithium",           "赣锋锂业",      "新能源"),
    ("300014", "EVE Energy",                "亿纬锂能",      "新能源"),
    ("300274", "Sungrow Power",             "阳光电源",      "新能源"),
    ("601012", "LONGi Green Energy",        "隆基绿能",      "光伏"),
    ("002129", "TCL Zhonghuan",             "TCL 中环",      "光伏"),
    ("600438", "Tongwei Co.",               "通威股份",      "光伏"),
    ("688599", "Trina Solar",               "天合光能",      "光伏"),
    ("603806", "Foster Electric",           "福斯特",        "光伏"),

    # ==== 科技 / 半导体 / 电子 ====
    ("002415", "Hikvision",                 "海康威视",      "安防"),
    ("000725", "BOE Technology",            "京东方A",       "面板"),
    ("002475", "Luxshare Precision",        "立讯精密",      "电子"),
    ("002049", "Unigroup Guoxin",           "紫光国微",      "半导体"),
    ("688981", "SMIC",                      "中芯国际",      "半导体"),
    ("603501", "Will Semiconductor",        "韦尔股份",      "半导体"),
    ("688012", "AMEC",                      "中微公司",      "半导体"),
    ("603986", "GigaDevice",                "兆易创新",      "半导体"),
    ("688256", "Cambricon",                 "寒武纪",        "半导体"),
    ("002371", "Naura Technology",          "北方华创",      "半导体"),
    ("300782", "Maxscend Microelectronics", "卓胜微",        "半导体"),
    ("600703", "Sanan Optoelectronics",     "三安光电",      "半导体"),
    ("688111", "Kingsoft Office",           "金山办公",      "软件"),
    ("300059", "East Money",                "东方财富",      "金融科技"),
    ("300308", "Accelink Technologies",     "中际旭创",      "光通信"),
    ("002241", "GoerTek",                   "歌尔股份",      "电子"),
    ("002027", "Focus Media",               "分众传媒",      "传媒"),
    ("300413", "Mango Excellent Media",     "芒果超媒",      "传媒"),

    # ==== 医药 / 生物 ====
    ("600276", "Hengrui Pharmaceuticals",   "恒瑞医药",      "医药"),
    ("300760", "Mindray Medical",           "迈瑞医疗",      "医疗器械"),
    ("600196", "Shanghai Fosun Pharma",     "复星医药",      "医药"),
    ("603259", "WuXi AppTec",               "药明康德",      "医药"),
    ("300122", "Chongqing Zhifei Bio",      "智飞生物",      "医药"),
    ("300347", "Tigermed",                  "泰格医药",      "医药"),
    ("000661", "Changchun High-Tech",       "长春高新",      "医药"),
    ("000538", "Yunnan Baiyao",             "云南白药",      "中药"),
    ("600085", "Tong Ren Tang",             "同仁堂",        "中药"),
    ("300015", "Aier Eye Hospital",         "爱尔眼科",      "医疗"),
    ("002714", "Muyuan Foods",              "牧原股份",      "农业"),
    ("000876", "New Hope Liuhe",            "新希望",        "农业"),

    # ==== 化工 / 材料 / 有色 ====
    ("600309", "Wanhua Chemical",           "万华化学",      "化工"),
    ("002001", "Zhejiang NHU",              "新和成",        "化工"),
    ("600019", "Baoshan Iron & Steel",      "宝钢股份",      "钢铁"),
    ("601600", "Chalco",                    "中国铝业",      "有色"),
    ("601899", "Zijin Mining",              "紫金矿业",      "有色"),
    ("600111", "China Northern Rare Earth", "北方稀土",      "有色"),
    ("000792", "Qinghai Salt Lake",         "盐湖股份",      "化工"),
    ("600585", "Anhui Conch Cement",        "海螺水泥",      "建材"),

    # ==== 能源 / 基建 / 电信 ====
    ("601857", "PetroChina",                "中国石油",      "能源"),
    ("600028", "Sinopec",                   "中国石化",      "能源"),
    ("601088", "China Shenhua",             "中国神华",      "煤炭"),
    ("601225", "Shaanxi Coal",              "陕西煤业",      "煤炭"),
    ("601985", "China National Nuclear",    "中国核电",      "电力"),
    ("600900", "China Yangtze Power",       "长江电力",      "电力"),
    ("600089", "TBEA Co.",                  "特变电工",      "电力设备"),
    ("601668", "China State Construction",  "中国建筑",      "建筑"),
    ("601390", "China Railway",             "中国中铁",      "建筑"),
    ("601186", "CRCC",                      "中国铁建",      "建筑"),
    ("601766", "CRRC Corporation",          "中国中车",      "机械"),
    ("600031", "SANY Heavy Industry",       "三一重工",      "机械"),
    ("000063", "ZTE Corporation",           "中兴通讯",      "通信"),
    ("600050", "China Unicom",              "中国联通",      "电信"),
    ("600941", "China Mobile",              "中国移动",      "电信"),
    ("601728", "China Telecom",             "中国电信",      "电信"),
    ("601111", "Air China",                 "中国国航",      "航空"),
    ("600029", "China Southern Airlines",   "南方航空",      "航空"),
    ("600009", "Shanghai Airport",          "上海机场",      "交通"),

    # ==== 地产 / 家电 / 其他 ====
    ("000002", "Vanke",                     "万科A",         "地产"),
    ("600048", "Poly Developments",         "保利发展",      "地产"),
    ("001979", "China Merchants Shekou",    "招商蛇口",      "地产"),
    ("600690", "Haier Smart Home",          "海尔智家",      "家电"),
    ("000651", "Gree Electric",             "格力电器",      "家电"),
    ("000333", "Midea Group",               "美的集团",      "家电"),
]

# ---------------------------------------------------------------------------
# 港股精选（~100 只，5 位代码格式，覆盖主板恒生成分与高关注度标的）
# 字段：5 位代码, 英文名, 中文名, 板块
# ---------------------------------------------------------------------------

HK_POPULAR_STOCKS: list[tuple[str, str, str, str]] = [
    # ==== 互联网 / 科技 ====
    ("00700", "Tencent Holdings",           "腾讯控股",      "互联网"),
    ("09988", "Alibaba Group (HK)",         "阿里巴巴",      "互联网"),
    ("03690", "Meituan",                    "美团",          "互联网"),
    ("09618", "JD.com (HK)",                "京东集团",      "互联网"),
    ("09888", "Baidu (HK)",                 "百度集团",      "互联网"),
    ("01024", "Kuaishou Technology",        "快手",          "互联网"),
    ("09999", "NetEase (HK)",               "网易",          "互联网"),
    ("09626", "Bilibili (HK)",              "哔哩哔哩",      "互联网"),
    ("09961", "Trip.com Group (HK)",        "携程集团",      "互联网"),
    ("01797", "New Oriental Online",        "东方甄选",      "互联网"),
    ("06060", "ZhongAn Online",             "众安在线",      "互联网"),
    ("03888", "Kingsoft Corporation",       "金山软件",      "软件"),
    ("00992", "Lenovo Group",               "联想集团",      "硬件"),
    ("00763", "ZTE Corporation (HK)",       "中兴通讯",      "通信"),
    ("00522", "ASMPT",                      "ASM 太平洋",    "半导体"),

    # ==== 汽车 / 新能源车 ====
    ("02015", "Li Auto (HK)",               "理想汽车",      "汽车"),
    ("09868", "XPeng (HK)",                 "小鹏汽车",      "汽车"),
    ("09866", "NIO (HK)",                   "蔚来",          "汽车"),
    ("01211", "BYD Company (HK)",           "比亚迪股份",    "汽车"),
    ("00175", "Geely Automobile",           "吉利汽车",      "汽车"),
    ("01114", "Brilliance China Auto",      "华晨中国",      "汽车"),
    ("02238", "Guangzhou Automobile",       "广汽集团",      "汽车"),

    # ==== 消费 / 零售 / 餐饮 ====
    ("01810", "Xiaomi Corporation",         "小米集团",      "硬件"),
    ("09992", "Pop Mart International",     "泡泡玛特",      "消费"),
    ("06862", "Haidilao International",     "海底捞",        "餐饮"),
    ("02020", "Anta Sports",                "安踏体育",      "消费"),
    ("02331", "Li Ning Company",            "李宁",          "消费"),
    ("01929", "Chow Tai Fook Jewellery",    "周大福",        "消费"),
    ("06186", "China Feihe",                "中国飞鹤",      "食品"),
    ("00322", "Tingyi",                     "康师傅控股",    "食品"),
    ("00151", "Want Want China",            "中国旺旺",      "食品"),
    ("02319", "China Mengniu Dairy",        "蒙牛乳业",      "食品"),
    ("00291", "China Resources Beer",       "华润啤酒",      "食品饮料"),
    ("01876", "Budweiser APAC",             "百威亚太",      "食品饮料"),
    ("00168", "Tsingtao Brewery (HK)",      "青岛啤酒股份",  "食品饮料"),
    ("01044", "Hengan International",       "恒安国际",      "日用"),
    ("06098", "Country Garden Services",    "碧桂园服务",    "服务"),

    # ==== 半导体 / 电子 ====
    ("02382", "Sunny Optical Technology",   "舜宇光学科技",  "电子"),
    ("00981", "SMIC (HK)",                  "中芯国际",      "半导体"),
    ("01347", "Hua Hong Semiconductor",     "华虹半导体",    "半导体"),
    ("06690", "Haier Smart Home (HK)",      "海尔智家",      "家电"),

    # ==== 金融 / 银行 / 保险 ====
    ("00005", "HSBC Holdings",              "汇丰控股",      "银行"),
    ("00011", "Hang Seng Bank",             "恒生银行",      "银行"),
    ("02888", "Standard Chartered",         "渣打集团",      "银行"),
    ("00939", "CCB (HK)",                   "建设银行",      "银行"),
    ("01398", "ICBC (HK)",                  "工商银行",      "银行"),
    ("03988", "Bank of China (HK)",         "中国银行",      "银行"),
    ("01288", "ABC (HK)",                   "农业银行",      "银行"),
    ("03968", "China Merchants Bank (HK)",  "招商银行",      "银行"),
    ("01658", "Postal Savings Bank",        "邮储银行",      "银行"),
    ("02318", "Ping An Insurance (HK)",     "中国平安",      "保险"),
    ("02628", "China Life (HK)",            "中国人寿",      "保险"),
    ("01299", "AIA Group",                  "友邦保险",      "保险"),
    ("00388", "HKEX",                       "香港交易所",    "交易所"),

    # ==== 能源 / 电信 / 公用 ====
    ("00883", "CNOOC",                      "中国海洋石油",  "能源"),
    ("00857", "PetroChina (HK)",            "中国石油股份",  "能源"),
    ("00386", "Sinopec (HK)",               "中国石油化工",  "能源"),
    ("01088", "China Shenhua (HK)",         "中国神华",      "煤炭"),
    ("00902", "Huaneng Power",              "华能国际",      "电力"),
    ("00941", "China Mobile (HK)",          "中国移动",      "电信"),
    ("00762", "China Unicom (HK)",          "中国联通",      "电信"),
    ("00728", "China Telecom (HK)",         "中国电信",      "电信"),
    ("00002", "CLP Holdings",               "中电控股",      "电力"),
    ("00003", "Hong Kong and China Gas",    "香港中华煤气",  "公用"),
    ("00006", "Power Assets",               "电能实业",      "公用"),
    ("00066", "MTR Corporation",            "港铁公司",      "交通"),

    # ==== 医药 / 生物 ====
    ("01093", "CSPC Pharmaceutical",        "石药集团",      "医药"),
    ("01177", "Sino Biopharmaceutical",     "中国生物制药",  "医药"),
    ("02269", "Wuxi Biologics",             "药明生物",      "医药"),
    ("02359", "WuXi AppTec (HK)",           "药明康德",      "医药"),
    ("01801", "Innovent Biologics",         "信达生物",      "医药"),
    ("06160", "BeiGene",                    "百济神州",      "医药"),
    ("01099", "Sinopharm Group",            "国药控股",      "医药"),
    ("01833", "Ping An Healthcare",         "平安好医生",    "医药"),
    ("06078", "JD Health",                  "京东健康",      "医药"),
    ("06821", "Asymchem Laboratories",      "凯莱英",        "医药"),

    # ==== 地产 / 综合 ====
    ("00001", "CK Hutchison",               "长和",          "综合"),
    ("00016", "Sun Hung Kai Properties",    "新鸿基地产",    "地产"),
    ("01113", "CK Asset Holdings",          "长实集团",      "地产"),
    ("01038", "CK Infrastructure",          "长江基建",      "基建"),
    ("00017", "New World Development",      "新世界发展",    "地产"),
    ("00083", "Sino Land",                  "信和置业",      "地产"),
    ("00012", "Henderson Land",             "恒基地产",      "地产"),
    ("01109", "China Resources Land",       "华润置地",      "地产"),
    ("00688", "China Overseas Land",        "中国海外发展",  "地产"),
    ("01972", "Swire Properties",           "太古地产",      "地产"),

    # ==== 工业 / 有色 / 其他 ====
    ("00669", "Techtronic Industries",      "创科实业",      "工业"),
    ("02899", "Zijin Mining (HK)",          "紫金矿业",      "有色"),
    ("01378", "China Hongqiao",             "中国宏桥",      "有色"),
    ("02689", "Nine Dragons Paper",         "玖龙纸业",      "造纸"),
    ("01128", "Wynn Macau",                 "永利澳门",      "博彩"),
    ("00027", "Galaxy Entertainment",       "银河娱乐",      "博彩"),
    ("01928", "Sands China",                "金沙中国",      "博彩"),
]

# ---------------------------------------------------------------------------
# 市场 → 数据表 映射
# ---------------------------------------------------------------------------

_MARKET_TABLES: dict[str, list[tuple[str, str, str, str]]] = {
    "A":  A_POPULAR_STOCKS,
    "HK": HK_POPULAR_STOCKS,
    "US": US_POPULAR_STOCKS,
}

_MARKET_SHORT = {"A": "A股", "HK": "港股", "US": "美股"}


# ---------------------------------------------------------------------------
# 搜索 / 目录
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """统一大小写、去空白，供匹配用。"""
    return "".join(str(s).lower().split())


def _format_label(market: str, code: str, name_zh: str, name_en: str) -> str:
    """multiselect 选项显示用的长标签（同时也是模糊匹配的关键字来源）。"""
    return f"{code} · {name_zh} · {name_en} · {_MARKET_SHORT[market]}"


def search_all(
    keyword: str,
    limit: int = 20,
    markets: Optional[Sequence[str]] = None,
) -> List[Dict[str, str]]:
    """
    跨市场模糊搜索，按命中精度排序。

    匹配优先级
    ----------
    0. code 完全相等（忽略大小写）
    1. code 前缀命中（如 "600" → A 股六系列；"AAP" → AAPL）
    2. 中文名前缀命中（如 "苹果" → AAPL；"贵州" → 600519）
    3. 英文名前缀命中
    4. 子串命中（出现在 code / 中英文名任一位置）

    参数
    ----
    keyword : 关键词（中文 / 英文 / 代码 片段）
    limit   : 最多返回条数
    markets : 过滤市场，例如 ["US"]；None 表示三市场全搜

    返回
    ----
    [{"market","code","name_en","name_zh","sector","label"}, ...]
    """
    kw = _normalize(keyword)
    if not kw:
        return []

    active_markets = list(markets) if markets else list(_MARKET_TABLES.keys())
    scored: list[tuple[int, int, dict]] = []

    for m in active_markets:
        for code, name_en, name_zh, sector in _MARKET_TABLES.get(m, []):
            c_key  = _normalize(code)
            en_key = _normalize(name_en)
            zh_key = _normalize(name_zh)

            if c_key == kw:
                score = 0
            elif c_key.startswith(kw):
                score = 1
            elif zh_key.startswith(kw):
                score = 2
            elif en_key.startswith(kw):
                score = 3
            elif kw in c_key or kw in en_key or kw in zh_key:
                score = 4
            else:
                continue
            market_order = {"A": 0, "HK": 1, "US": 2}.get(m, 3)
            scored.append((
                score, market_order,
                {
                    "market":  m,
                    "code":    code,
                    "name_en": name_en,
                    "name_zh": name_zh,
                    "sector":  sector,
                    "label":   _format_label(m, code, name_zh, name_en),
                },
            ))

    scored.sort(key=lambda x: (x[0], x[1], x[2]["code"]))
    return [item for _, _, item in scored[:limit]]


# 向后兼容：旧 sidebar 代码可能仍引用 search_us
def search_us(keyword: str, limit: int = 20) -> List[Dict[str, str]]:
    """已弃用：请使用 search_all(..., markets=['US'])。保留以防旧代码依赖。"""
    results = search_all(keyword, limit=limit, markets=["US"])
    return [
        {"ticker": r["code"], "name_en": r["name_en"],
         "name_zh": r["name_zh"], "sector": r["sector"]}
        for r in results
    ]


def build_catalog(
    markets: Optional[Sequence[str]] = None,
) -> List[Dict[str, str]]:
    """
    构建完整目录，供 `st.multiselect` 的 options 使用。

    返回结构：每项含 code / market / label，UI 只需用 `label` 显示即可，
    用户选择后调用方通过 `code` + `market` 做数据路由。

    顺序：A 股 → 港股 → 美股，各自内部按代码字典序。
    """
    active = list(markets) if markets else list(_MARKET_TABLES.keys())
    out: list[dict] = []
    for m in active:
        for code, name_en, name_zh, sector in sorted(_MARKET_TABLES[m]):
            out.append({
                "code":   code,
                "market": m,
                "label":  _format_label(m, code, name_zh, name_en),
                "sector": sector,
            })
    return out


def lookup_label(code: str) -> Optional[str]:
    """
    根据代码反查目录中对应的 label（用于 multiselect 的 format_func）。
    未命中时返回 None，调用方应自行 fallback 到原始 code。
    """
    needle = _normalize(code)
    for m, rows in _MARKET_TABLES.items():
        for c, name_en, name_zh, _sec in rows:
            if _normalize(c) == needle:
                return _format_label(m, c, name_zh, name_en)
    return None
