# Encumbrance, Cargo, and Ordinary Carried Mass

Numerical authority lives in `data/mechanics/encumbrance.json`. Only external mass actually carried in the scene contributes to burden. Body mass and height are handled by body mechanics and are not re-added as carried load. Custody changes immediately change carried mass.

The resolver must calculate load support, distribution multiplier, effective burden, movement/agility/stealth penalties, fatigue multiplier, and water-walking burden exactly from the structured formulas. No dedicated weight-release combat action exists unless later registered as a real mechanic.
