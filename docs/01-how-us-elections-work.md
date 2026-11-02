# 1. How US Elections Work (Decoded)

[Back to README](../README.md)

## 1.1 The big idea: there is no single "US election system"

The Constitution gives **states** the power to run elections (Article I, Section 4), with Congress able to set some rules. So the US actually has **50 state systems plus DC and territories**, administered by roughly **10,000 local jurisdictions** (counties, cities, townships). Federal law sets the floor: who may vote (citizens 18+), anti-discrimination rules (Voting Rights Act), registration standards (National Voter Registration Act), and minimum standards for equipment and provisional ballots (Help America Vote Act).

```
US Constitution
   |
   +-- Federal law (VRA, NVRA, HAVA, UOCAVA, FECA)
   |
   +-- 50 states + DC  (election codes, Secretary of State / election boards)
          |
          +-- ~10,000 local election offices (counties/towns: registration, polling places, counting)
```

## 1.2 What gets elected, and how often

| Office | Number | Term | Elected by | 2026? |
|---|---|---|---|---|
| President + VP | 1 + 1 | 4 yrs | Electoral College (538 electors) | No (next: 2028) |
| US Senator | 100 (2 per state) | 6 yrs, staggered in 3 "classes" | Statewide popular vote | Yes: Class 2 (33 seats) + 2 specials (FL, OH) = 35 |
| US Representative | 435 voting seats | 2 yrs | Single-member districts | Yes: all 435 |
| Governor | 50 | 4 yrs (2 in NH, VT) | Statewide | Yes: 36 |
| State legislators, AGs, Secretaries of State, judges, mayors, school boards | thousands | varies | varies | Yes, many |

A **midterm** is the federal election held halfway through a presidential term. Historically the president's party loses seats: on average about 25 House seats (per Northeastern University analysis cited in [docs/02](02-midterms-2026-analysis.md)).

## 1.3 Census -> apportionment -> redistricting

1. **Census (every 10 years, Article I, Section 2):** the Census Bureau counts every resident, including non-citizens.
2. **Apportionment:** the 435 House seats are divided among states by population (method of equal proportions). The 2020 Census set seats for the 2022 to 2030 elections. Each state also gets electoral votes = House seats + 2.
3. **Redistricting:** each state draws its own district lines, usually by the legislature (sometimes commissions or courts). Districts must have equal population and comply with the Voting Rights Act.
4. **Gerrymandering:** drawing lines to favor a party. The Supreme Court ruled in *Rucho v. Common Cause* (2019) that federal courts will not police *partisan* gerrymandering, leaving it to states.
5. **2025-26 twist:** an unusual wave of **mid-decade redistricting** (Texas, California's Prop 50, Missouri, North Carolina, Ohio, Utah and others). Details in [docs/02](02-midterms-2026-analysis.md#6-the-redistricting-wars).

Data: [Census apportionment results](https://www.census.gov/data/tables/2020/dec/2020-apportionment-data.html), [Census redistricting data program](https://www.census.gov/programs-surveys/decennial-census/about/rdo.html).

## 1.4 The election cycle step by step

| Stage | What happens | Variation by state |
|---|---|---|
| Candidate filing | Candidates qualify via signatures/fees | Deadlines differ widely |
| Primaries | Parties choose nominees (Mar-Sep 2026) | Closed, open, semi-open, top-two (CA, WA), top-four + RCV (AK), runoffs (GA, TX and others) |
| Voter registration | Citizens register | Same-day registration in ~20 states; automatic registration via DMV in many (including Massachusetts); ND has no registration |
| Early and mail voting | Ballots cast before Election Day | Universal vote-by-mail in some states (e.g. CA, CO, OR, WA, UT, NV); excuse required in a few |
| Election Day | First Tuesday after first Monday in November (Nov 3, 2026) | Poll hours set by state |
| Counting | Precincts and central counts tabulate | Some states cannot process mail ballots before Election Day, so counts take longer |
| Ranked-choice / runoffs | If no majority | Maine and Alaska use RCV for federal races; Georgia holds runoffs if no one passes 50% |
| Canvass and certification | Officials verify and certify (days to weeks) | Recounts triggered by margin thresholds |
| Seating | New Congress sworn in Jan 3, 2027; governors mostly in January | |

**Media "calls" are not official results.** The AP and Decision Desk HQ project winners from vote counts and models; official results come from state certification.

## 1.5 Who can vote

- US citizens, 18+ by Election Day, meeting state residency rules, and registered (except North Dakota).
- Some states restrict voting for people with felony convictions; rules range from never losing the right (Maine, Vermont, DC) to lifetime bans for some offenses.
- **Non-citizens cannot vote in federal or state elections.** A handful of municipalities allow non-citizens to vote in purely local elections, and several states have constitutional bans on even that. See [docs/07](07-non-citizens-students-and-workers.md).

## 1.6 How Congress control works

- **House:** majority = 218. The majority elects the Speaker and controls every committee, the floor agenda and subpoena power.
- **Senate:** majority = 51, or 50 + the Vice President's tie-breaking vote. The majority leader controls the floor; the **filibuster** means most legislation needs 60 votes. Senate confirms judges, cabinet officials and ratifies treaties.
- **Divided government** (one party has the White House, the other at least one chamber) usually means less legislation, more oversight investigations, and high-stakes budget fights (shutdown risk).

## 1.7 The Electoral College (presidential years only)

538 electors; 270 to win. 48 states plus DC award all electors to the statewide winner; Maine and Nebraska split by congressional district. Not on the ballot in 2026, but governors and secretaries of state elected in 2026 will administer the 2028 presidential election.

## 1.8 Money in elections

- The **Federal Election Commission (FEC)** enforces federal campaign finance law. Individuals can give limited amounts to candidates; Super PACs can raise and spend unlimited sums independently (*Citizens United*, 2010).
- **Foreign nationals (anyone who is not a citizen or green card holder) may not contribute or spend money in any US election: federal, state or local** (52 U.S.C. 30121). They may volunteer without pay.
- Data: [FEC campaign finance data](https://www.fec.gov/data/), [OpenSecrets](https://www.opensecrets.org/).

## 1.9 Glossary

| Term | Meaning |
|---|---|
| PVI | Cook Partisan Voter Index: how a district/state leans relative to the nation |
| Generic ballot | Poll question: "Which party's candidate for Congress would you vote for?" |
| Toss Up / Lean / Likely / Safe | Race-rating tiers from most to least competitive |
| RCV | Ranked-choice voting |
| Wave election | A large, broad swing toward one party |
| Lame duck | The period between the election and the new Congress (Nov 2026 to Jan 3, 2027) |
| Canvass | The official review and tally of ballots before certification |
| Provisional ballot | Ballot cast when eligibility needs checking; counted once verified |

## Sources

- [USA.gov: How elections work](https://www.usa.gov/how-elections-work)
- [vote.gov](https://vote.gov/) (official registration portal)
- [NCSL election laws and procedures](https://www.ncsl.org/elections-and-campaigns)
- [US Election Assistance Commission](https://www.eac.gov/)
- [FEC: Foreign nationals](https://www.fec.gov/updates/foreign-nationals/)
- [Census Bureau apportionment](https://www.census.gov/data/tables/2020/dec/2020-apportionment-data.html)

---

> **Disclaimer:** This document is only a collection of publicly available resources, data and forecasts. The creator is neutral and does not support or oppose any political party, candidate or ideology. Figures come from the linked sources and may change or contain errors. Not legal, immigration or financial advice.
