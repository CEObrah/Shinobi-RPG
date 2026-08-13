# Economy Balance

This document explains the current balance model. Mechanical authority lives in `game/data/mechanics/economy.json`; live balances and recurring campaign flows live in `state/world/economies-and-mission-markets.json` plus `state/inventory/registry.json`.

## Design goals

1. Mission pay must feel useful to a shinobi instead of merely reimbursing routine kunai.
2. Official duty must not force personnel to retail-buy normal issued equipment.
3. Personal spending, village finance, country finance, and estate finance must use the same conserved `currency.ryo` authority without requiring one wallet transaction for every background NPC.
4. Routine paid missions should not be structurally loss-making at the listed typical fee/reward point.
5. Strategic institutions may run surpluses, deficits, or arrears, but no transfer may create negative money or mint currency.
6. Major assets and projects must exist on a scale compatible with village budgets rather than using player-scale prices.

## Player-scale reference

A standard shinobi loadout currently has a base retail replacement value of 22,800 ryō. The obvious routine expendable combat portion is 2,800 ryō: eight kunai, twelve shuriken, and two smoke bombs. The remaining 20,000 ryō is predominantly reusable equipment.

Official village/institution missions issue approved standard gear from institutional stock. Normal mission pay is therefore compensation for risk, time, expertise, and service. It is not expected to be consumed merely restoring ordinary issued kunai and shuriken.

Reference monthly salary and living-cost anchors:

| Reference | Ryō/month |
|---|---:|
| Genin salary | 35,000 |
| Chūnin salary | 60,000 |
| Special jōnin salary | 90,000 |
| Jōnin salary | 140,000 |
| Elite jōnin salary | 180,000 |
| ANBU salary | 170,000 |
| Shinobi adult living cost | 35,000 |
| Civilian adult living cost | 25,000 |
| Child living cost | 15,000 |

The salary table is a balance/reference authority. Routine NPC payroll remains in macro village operations unless an exact personal account matters to play. Wei Tang is exact because the player's personal finances matter.

## Mission fee and reward balance

The participant bonus is per exact participant. The client fee is the gross mission-market fee reference. At the typical four-person-team reference, the typical client fee is required to cover four typical participant bonuses plus the typical operational allowance.

| Rank | Client fee typical | Participant bonus typical | Four bonuses + allowance | Gross headroom |
|---|---:|---:|---:|---:|
| D | 40,000 | 8,000 | 34,000 | 6,000 |
| C | 150,000 | 28,000 | 118,000 | 32,000 |
| B | 400,000 | 70,000 | 298,000 | 102,000 |
| A | 1,300,000 | 250,000 | 1,060,000 | 240,000 |
| S | 8,000,000 | 1,500,000 | 6,250,000 | 1,750,000 |

The maximum client fee for every rank also covers four maximum participant bonuses plus the typical operational allowance. This prevents the price table itself from making normal contracted missions a guaranteed loss.

Mission rewards are not minted. When an exact mission promises a currency reward, the runtime moves the maximum potential promised reward into mission escrow at creation. Settlement pays applicable rewards from that escrow and returns unused escrow to the funding account.

## Food, lodging, and ordinary services

Reference public prices include:

| Service | Base price |
|---|---:|
| Tea/snack | 100 ryō |
| Simple meal | 120 ryō |
| Ramen | 180 ryō |
| Restaurant meal | 350 ryō |
| Premium meal | 800 ryō |
| Public bath | 180 ryō |
| Bunk | 800 ryō/night |
| Normal room | 1,800 ryō/night |
| Premium room | 4,500 ryō/night |
| Medical exam | 1,500 ryō |
| Minor treatment | 5,000 ryō |
| Surgery day | 50,000 ryō |
| Training ground | 500 ryō/hour |
| Dojo instruction | 2,500 ryō/hour |
| Specialist instruction | 10,000 ryō/hour |

A typical 28,000-ryō C-rank participant bonus therefore buys far more than a meal or routine local expense. Even if someone privately replaced the 2,800-ryō expendable portion of a standard kit, the typical C-rank bonus would still retain 25,200 ryō. Official missions normally make that replacement an institutional-stock expense instead.

## Item markets

All 84 catalog items have one base-price record. Open goods may trade within 60% to 160% of base. Controlled goods may trade within 75% to 250% of base. Institutional-only and not-for-sale goods cannot use ordinary purchase contracts.

Open-market stock is materialized only where scarcity matters to current play. Other markets remain cold until needed. Routine open goods can be bought directly from aggregate market stock at the authoritative base price, so buying ten kunai does not create an offer, acceptance, shop ledger, merchant wallet, payroll entry, or business-profit simulation. Payment settles into the relevant aggregate background economy.

The exact seller/buyer contract path is reserved for negotiation, controlled or scarce goods, private transactions, and other cases where the agreement itself matters. Ordinary food and services use direct payment with the same aggregate settlement rule.

## Great-village operating scale

Current major-village operating references are deliberately close on a per-shinobi basis rather than arbitrary absolute numbers:

| Village | Force personnel | Monthly operations | Operations per force member |
|---|---:|---:|---:|
| Konoha | 12,000 | 1.05B | 87,500 |
| Suna | 7,000 | 520M | 74,286 |
| Kiri | 8,500 | 780M | 91,765 |
| Kumo | 10,000 | 950M | 95,000 |
| Iwa | 10,500 | 950M | 90,476 |

Those operating flows represent aggregate payroll, facilities, ordinary stock consumption/replacement, administration, training, healthcare, mission support, and other routine costs. They do not require thousands of exact salary transactions.

Current recurring village net before ad-hoc exact missions/projects and new commerce:

| Treasury | Approx. net/month |
|---|---:|
| Konoha | +148M |
| Suna | -30M |
| Kiri | +20M |
| Kumo | +150M |
| Iwa | +70M |
| Ame | +40M |
| Kusa | +20M |
| Oto | -10M |
| Taki | +10M |
| Yuga | -5M |
| Iron samurai | +50M |

Suna's deficit is deliberate funding pressure, not an accounting error. Its initial treasury gives roughly seven years of runway at the current unchanged baseline. Oto and Yuga are also mildly structurally pressured. The scheduler records arrears rather than allowing a treasury to go negative if conditions worsen.

## Country scale

Country background economies, country treasuries, and village treasuries are separate at the level needed for solvency decisions. The runtime does not separately simulate every tax receipt, payroll run, shop sale, rent payment, or civilian purchase. Those routine flows are netted into one background-budget movement per tracked treasury/house. Village subsidies and other strategically meaningful institutional transfers remain explicit.

The current monthly budget representation therefore preserves the same treasury net positions with far fewer transfers. Aggregate background-economy holders are clearing pools, not simulated businesses or household balance sheets.

Country/private balances are intentionally much larger than player or village cash. Great-country private pools begin in the hundreds of billions of ryō, country treasuries in the tens of billions, and major village treasuries in the low single- to low double-digit billions.

## Sword Manor and House Tang

Sword Manor's current replacement-value planning anchor is 480M ryō. It is not an automatic sale price.

House Tang currently has:

- 12M ryō opening liquid balance
- 1.8M/month Konoha service/training contract income
- 1.4M/month private contract income
- 2.9M/month Sword Manor and household operating/upkeep outflow
- approximately +300k/month recurring net before ad-hoc activity

The 2.9M monthly outflow represents a large staffed compound, household support, upkeep, security/training infrastructure, and routine procurement. It is intentionally far above a single person's living cost and far below the replacement value of the entire estate.

## Projects and capital

Institution projects pay both material stock costs and a contractor cash cost before work begins. Current cash references are 4M to 12M for bounded storage/training/security/medical/custody improvements. These are upgrades/modules, not prices for constructing an entire hidden village.

Capital/replacement anchors include approximately:

- modest home: 4M ryō
- large residence: 20M
- small estate: 60M
- fortified manor: 250M
- elite compound: 500M
- academy wing: 20M
- hospital wing: 35M
- major security upgrade: 30M
- minor hidden-village infrastructure: 3B
- great hidden-village infrastructure: 30B

These values prevent project and real-estate prices from accidentally living on the same scale as lunch or one C-rank mission.

## Conservation and failure behavior

- Currency transfers fail closed on insufficient funds.
- Mission rewards require funded escrow.
- Service purchases debit the payer and settle ordinary provider revenue to the lawful aggregate/local financial holder.
- Item purchases debit the buyer, settle payment to the seller's lawful financial holder, and reduce real market/source stock.
- Monthly macro shortfalls become arrears.
- Institutional projects pay cash and stock before work begins.
- Standard official mission issue consumes institutional stock rather than personal cash.
- Exact personal accounting is used only where the person's finances matter; background household/village activity remains aggregate.

This is deliberately a sparse economy. It gives money meaningful gameplay consequences without turning every restaurant, resident, payroll line, or storefront into a permanent autonomous accounting object.
