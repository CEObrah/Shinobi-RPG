# Items

`game/data/items/index.json` and its referenced item files are the authoritative item registry. Mechanically identical items share one catalog definition; instances reference the catalog ID.

Training changes how well a person uses an item. It does not rewrite the item's base mechanics.

Every active loadout and inventory reference must resolve before canonical play.

## Ordinary stock and currency

Ordinary fungible stock is conserved between registered stock owners and inventory holders. Issue, return, consume, and refit mutate those authorities directly; an equipment standard never creates its listed items.

Ordinary open-market stock may be purchased directly at its authoritative public base price without creating a contract object. Scarce, controlled, negotiated, or private sales use a purchase contract: seller offer, buyer acceptance, and final stock/currency exchange. Acceptance never reserves or creates goods. Completion rechecks stock and funds, moves the goods once, and settles payment to the seller's lawful financial holder.

Currency is conserved inventory. A contract, mission reward, price, or narrative statement never mints ryō. A payment succeeds only when its source holder actually owns enough `currency.ryo`.


## Prices and official issue

`game/data/mechanics/economy.json` owns public item prices and market-access rules. Official mission issue follows `game/rules/text/economy.md`: approved standard mission equipment comes from conserved institutional stock and is not charged to the individual shinobi as a retail purchase.
