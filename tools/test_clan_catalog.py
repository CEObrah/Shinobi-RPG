#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load(p): return json.loads((ROOT/p).read_text(encoding="utf-8"))
def fail(m): raise SystemExit("CLAN CATALOG TEST FAILED: "+m)
cat=load("game/data/clans/catalog.json"); profiles=cat.get("profiles",[])
if len(profiles)!=52: fail(f"expected 52 profiles, got {len(profiles)}")
if sorted(p.get("name") for p in profiles)!=['Aburame Clan', 'Akimichi Clan', 'Amagiri Clan', 'Chinoike Clan', 'Funato Clan', 'Fūma Clan', 'Fūma Clan (Land of Sound)', 'Hagoromo Clan', 'Hatake Clan', 'Hirasaka Clan', 'Hoshigaki Clan', 'Hyūga Clan', 'Hōki Family', 'Hōzuki Clan', 'Iburi Clan', 'Inuzuka Clan', 'Izuno Clan', "Jūgo's Clan", 'Kagetsu Family', 'Kaguya Clan', 'Kamizuru Clan', 'Karatachi Family', 'Kazekage Clan', 'Kedōin Clan', 'Kodon Clan', 'Kohaku Clan', 'Kumanoi Clan', 'Kurama Clan', 'Lee Clan', 'Nara Clan', 'Onikuma Clan', 'Rinha Clan', 'Ryū Clan', 'Sarutobi Clan', 'Sendō Clan', 'Senju Clan', 'Shiin Clan', 'Shimura Clan', 'Shirogane Clan', 'Taketori Clan', 'Tenrō Clan', 'Tsuchigumo Clan', 'Uchiha Clan', 'Uzumaki Clan', 'Wagarashi Family', 'Wasabi Family', 'Yamanaka Clan', 'Yoimura Clan', "Yota's Clan", 'Yotsuki Clan', 'Yuki Clan', 'Ōtsutsuki Clan']: fail("Narutopedia category membership mismatch")
byid={}
for p in profiles:
    cid=p.get("id")
    if not isinstance(cid,str) or not cid.startswith("clan.") or cid in byid: fail("bad clan id")
    byid[cid]=p
    if not p.get("article") or not p.get("media"): fail("source provenance missing for "+cid)
    for key in ("aff","institution","capability","training"):
        if not isinstance(p.get(key),list): fail("tag list missing for "+cid+":"+key)
if cat.get("training_policy",{}).get("membership_is_not_mastery") is not True: fail("no-free-mastery rule missing")
if "Only facts valid by campaign time" not in cat.get("source_policy",{}).get("continuity_rule",""): fail("timeline guard missing")
if byid["clan.otsutsuki"].get("model")!="nonhuman_special": fail("Otsutsuki must use special model")
for cid in ("clan.uchiha","clan.hyuga","clan.hozuki","clan.yotsuki","clan.kazekage","clan.kamizuru","clan.fuma_sound","clan.yuki"):
    if cid not in byid: fail("cross-world coverage missing "+cid)
repo=load("runtime/contracts/repository-map.json")
if repo.get("route_index",{}).get("clan_known_id")!="world": fail("clan route not registered")
world=load("runtime/contracts/repository-routes/world.json")
if "game/data/clans/catalog.json" not in world.get("routes",{}).get("clan_known_id",{}).get("r",[]): fail("catalog route missing")
router=load("runtime/contracts/rule-router.json")
if "game/rules/text/clans.md" not in router.get("domains",{}).get("clan_institution",[]): fail("clan rule domain missing")
models=load("game/rules/training/models.json")
if models.get("models",{}).get("training.cohort",{}).get("context_kind")!="cohort": fail("generic cohort training disappeared")
blank=load("runtime/contracts/blank-owner-index.json")
if blank.get("owners",{}).get("clan-institution")!="runtime/contracts/blank-owners/clan-institution.blank.json": fail("clan blank missing")
cidx=load("runtime/contracts/template-index-shards/c.json")
for target in ("clan-institution","clan-profile-catalog.v1"):
    if target not in cidx.get("templates",{}): fail("template missing "+target)
contract=load("runtime/contracts/system-contracts/forces_institutions.json")
if "clan-institution" not in contract.get("owner_templates",[]) or "state/clan/" not in contract.get("authority_paths",[]): fail("clan owner contract missing")
dirs=load("runtime/contracts/directory-map.json")
if dirs.get("dirs",{}).get("state/clan")!="mapped": fail("state/clan directory missing")
print("CLAN CATALOG TESTS OK")
