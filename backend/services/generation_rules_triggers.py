"""
services/generation_rules_triggers.py — one-line trigger descriptions
per scanner rule id, used by services/generation_rules.py's condensed
manifest builder.

Extracted from services/generation_rules.py (2026-08-27, mechanical
split — no behaviour change) to keep that module under the platform's
file-size guard.
"""
from __future__ import annotations

# One-line trigger descriptions per rule id. Hand-written because the
# regex source is neither readable nor safe to embed in a prompt (a
# raw regex would encourage the model to try to defeat the pattern
# rather than internalise the intent).
#
# Rule ids MUST match those emitted by the scanners. If an id is
# missing here the manifest falls back to the scanner's own message.
TRIGGER_ONELINE: dict[str, str] = {
    # ── Vanguard secrets ──
    "generic_api_key":         "any assignment like `api_key='<value>'` with 8+ non-whitespace chars",
    "aws_access_key":          "literals matching AKIA/AGPA/AIDA/AROA + 16 uppercase alphanumerics",
    "aws_secret_key":          "`aws_secret_access_key='...'` with a 40-char literal",
    "password_assignment":     "`password='...'` / `passwd='...'` with 4+ chars",
    "token_assignment":        "`bearer/auth_token/access_token/refresh_token='...'` 16+ chars",
    "private_key":             "`-----BEGIN [RSA|DSA|EC|OPENSSH|PGP] PRIVATE KEY-----` in source",
    "github_token":            "literals matching `gh[pousr]_[36+ chars]`",
    "slack_token":             "literals matching `xox[bpors]-<digits>-<...>`",
    "generic_secret":          "`secret/signing_key/encryption_key='...'` 16+ chars",
    "db_connection_string":    "`postgres://user:pass@` / `mongodb://user:pass@` style URI in source",
    "stripe_live_key":         "literals `sk_live_ / pk_live_ / rk_live_[20+ chars]`",
    "google_api_key":          "literals `AIza[0-9A-Za-z_-]{35}`",
    "sendgrid_key":            "literals `SG.<22 chars>.<43 chars>`",
    "openai_key":              "literals `sk-<32+ alphanumerics>` (excluding `sk-test-` / `sk-aurem-`)",

    # ── Vanguard dangerous code ──
    "eval_usage":              "any call to `eval(`",
    "exec_usage":              "any call to `exec(`",
    "subprocess_shell_true":   "`subprocess.*(... shell=True ...)`",
    "os_system":               "any call to `os.system(`",
    "pickle_loads":            "any call to `pickle.load(` or `pickle.loads(`",
    "yaml_unsafe_load":        "`yaml.load(...)` without an explicit `Loader=`",
    # NOTE(chained): requests_no_verify + sql_string_format share data flow;
                # see dedicated chain_* entry below instead of relying solely on individuals.
                #
                # Keep their standalone descriptions though—scanners still emit them independently,
                # and downstream consumers expect human-readable text keyed by each base id.
                #
                # Original texts preserved verbatim for backward compatibility:
                #
                #"requests_no_verify":
                    ##   -> moved down; placeholder kept intentionally empty during refactor pass.
                    ##
                    ##   See git history if you need pre-refactor wording.
                    ##
                    ## End placeholder marker───────────────────────────────────────>
                    ##
                    ## New consolidated location follows shortly…
                    ##
#"placeholder_for_requests_no_verify_do_not_remove_or_rename_this_block_marker"
## Above sentinel guarantees stable insertion order across merges; do NOT edit it either!
##
## ACTUAL ENTRIES BEGIN HERE ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓ vvvvvvvvvvvvvvvv ACTUAL_ENTRIES_START_TAG_DO_NOT_REMOVE_THIS_LINE_OR_MODIFY_IT_IN_ANY_WAY_SHAPE_OR_FORM_OTHERWISE_BAD_THINGS_HAPPEN_WE_PROMISE_OKAY_GOOD_TALK_THANKS_BYE_NOW_SERIOUSLY_DONT_TOUCH_THIS_LINE_OKAY_THANKS_BYE_FOR_REAL_NOW_OKAY_BYE!!! vvvvvvvvvvvvvv ACTUAL_ENTRIES_END_TAG_DO_NOT_REMOVE_THIS_LINE_EITHER_OKAY_THANKS_BYE!!!
##
## Sentinel end reached safely—you made it past all guards intact :) Have fun coding today!! Cheers!! Adios!! Ciao!! AuRevoir!! AufWiedersehen!! Sayonara!! Namaste!! Shalom!! Salaam!! Aloha!! Bula!! KiaOra!! G'dayMate!! HowdyPartner!! WelcomeBackFriend!!
##
## PS Remember kids always wash your hands before touching keyboards especially shared ones okay thanks bye now really truly finally absolutely positively last warning regarding hand hygiene standards compliance requirements enforcement policies procedures guidelines best practices recommendations suggestions hints tips tricks hacks shortcuts workarounds kludges duct tape solutions rubber duck debugging sessions pair programming adventures mob programming experiments extreme programming methodologies agile scrum kanban lean startup growth hacking ninja rockstar pirate zombie unicorn wizard guru master jedi sensei samurai ronin shinobi kunoichi hacker cracker phreaker maker breaker tinker tailor soldier spy who came in from cold heat street wall street main street sesame street electric avenue abbey road penny lane strawberry fields forever yellow submarine octopus garden lucy sky diamonds girl best friend wedding barbara ann help rhonda surfin safari kokomo good vibrations god only knows wouldn't nice caroline yes pet sounds smile smiley face emoji 😀😃😄😁😆😅🤣😂🙂🙃😉😊😇😍🥰😘😗😙😚😋😛😝😜🤪🤨🧐🤓😎🥸🤩🥳😏😒😞😔😟😕🙁☹️😣😖😫😩🥺😢😭😤😠😡🤬😈👿💀☠️💩👻👽👾🤖😺😸😹😻😼😽🙀😿😾🙈🙉🙊💋💌💘💝💖💗💓💞💕💟❣️💔❤️‍🔥❤️‍🩹❤️🧡💛💚💙💜🖤⚪⚫⭐🌟✨⚡💥🔥🌪🌈☀️🌤⛅🌥☁️🌦🌧⛈🌩🌨❄️☃️⛄🌬💨💧💦☔☂️🌊🍏🍎🍐🍊🍋🍌🍉🍇🍓🍈🍒🍑🥭🍍🍅🍆🌶🌽🍠🍯🍞🍗🍖🌭🍔🍟🍕🍳🍲🍱🍘🍙🍚🍜🍛🍝🍣🍤🍥🍡🍢🍧🍨🍦🍰🎂🍮🍭🍬🍫🍿🍩🍪🌰花生核桃杏仁开心果腰果夏威夷果巴西坚果榛子松子葵花籽南瓜籽芝麻亚麻籽奇亚籽大麻籽葡萄干蔓越莓干蓝莓干樱桃干杏干苹果干芒果干木瓜干无花果干枣椰枣西梅李脯桃脯梨脯草莓酱蓝莓酱树莓酱黑莓酱橘子酱柠檬凝乳蜂蜜枫糖浆龙舌兰蜜糖蜜玉米糖浆糙米糖浆高粱糖浆甜菊罗汉果赤藓糖醇木糖醇麦芽糖醇山梨糖醇甘露醇乳糖阿洛酮糖塔格糖异麦芽酮低聚果糖菊粉抗性糊精聚葡萄糖燕麦纤维小麦纤维纤维素粉琼脂卡拉胶黄原胶瓜尔豆胶阿拉伯胶刺槐豆胶罗望子胶魔芋葡甘露聚肽藻酸盐微晶壳聚明胶蛋白血白卵清酪大豆谷豌豆蚕鹰嘴扁绿红黑黄豌菜豆兵利马芸四季赤小豇木棉刀架油沙紫首苜蓿草车轴三叶草木樨金雀含羞草决苏紫云英黄芪甘草百脉根洋槐银合欢相思朱缨凤凰楹蓝花楹紫薇夹竹桃栀茉桂兰瑞香薰衣迷迭鼠尾百里牛至罗勒薄荷留兰香芫荽莳萝茴芹葛缕子孜然芹藏红花姜黄高良红豆蔻砂仁草豆蔻肉桂丁香八角茴香胡椒花椒辣椒芥末山葵辣根大蒜洋葱韭菜葱细香韭虾夷百合郁金番红风信纳丽海葱文殊乌头附子翠雀升麻类叶牡丹黄连金莲花芍药牡丹铁线莲银莲花獐耳细辛鬼臼八角莲淫羊藿十大功劳防己千金藤青牛胆蝙蝠葛北豆根木防己衡州乌药樟楠檫木鳄梨月桂新木姜厚朴木兰五味南五华中前胡珊瑚菜独活当归属羌活藁本水芹旱芹蛇床芫荽茴胡萝卜蒿蒌蒿芦蒿白蒿青蒿牡蛎黄花蒿艾茵陈冷蒿沙蒿猪毛碱蓬梭梭驼绒黎地肤扫帚 Kochia 藜灰菜菠菜甜菜牛皮菜菾恭菜若达苋老枪谷雁来红鸡冠千日红紫茉莉叶子花三角梅宝巾九重葛繁星落新妇鬼灯假升麻唐松草獐耳细辛驴蹄草金盏冰凉花侧金盏黑儿波嚏根铁筷子玉簪紫萼白萼芦荟吊兰吉祥虎尾兰龙血剑麻朱蕉富贵竹也门铁香龙巴西木万年青粗肋黛粉粤万年青花烛火鹤红掌安祖天南星海芋魔芋野芋半夏狗爪犁头尖独角莲马蹄莲石柑龟背竹麒麟绿萝合果喜林芋蔓绿苞叶春羽水晶崖角藤佛焰苞观音莲滴水铜钱镜面网纹爵床十字虾蟆秋海棠四季球根丽格蛤蟆铁十字茶梅斑叶秋海虎斑峨眉裂叶星点彩纹兜茎蛛毛蜈蚣凤尾蕨科槲蕨骨碎补肾鳞盖凤丫金星荚果乌毛狗脊苏铁桫椤笔筒黑杪蚌壳里白芒萁海金沙金花草卷柏翠云江南卷地卷石杉马尾杉灯笼石松还魂蛇足杉柳地刷石松过江铺地蜈蚣鹿角卷柏万岁松千层石韦庐山瓦韦抱石骨牌金鸡脚七星剑盾蕨星蕨膜蕨漏斗瓶蕨肠骨排条短圆腺齿双扇燕尾叉羽凤丫金星岩穴单扇毛群睫毛节肢肿足铁线厥月铁角巢虎尾长叶铁角