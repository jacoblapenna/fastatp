# Hoot: tool calling as signed records

*A skeleton: the cast, the programs, and the ride's three commands, so the
lab and the page can be played end to end. The tutorial itself is written on
top of this.*

<!-- @demo machines alice concierge bank hoot jitney cab-17 -->
<!-- @demo peer alice rider.py -->
<!-- @demo peer concierge concierge.py -->
<!-- @demo peer bank bank.py -->
<!-- @demo peer hoot company.py -->
<!-- @demo peer jitney company.py -->
<!-- @demo peer cab-17 cab.py -->

<!-- @demo start -->
<!-- @demo chapter 1 "The ride" -->
<!-- @demo blurb alice asks for a ride, and the agents do the rest, every step a signed record. -->

## The ride

alice lets the concierge have the bank hold up to $40 for a ride.

```bash
./lab.sh alice curl -s -X POST localhost/allow -d '{"agent": "did:web:concierge.lan", "bank": "did:web:bank.lan", "account": "checking", "per_ride": 4000}'
```

alice asks for the ride, in a private note.

```bash
./lab.sh alice curl -s -X POST localhost/commit -d '{"repo": "channel", "text": "Get me a ride to the airport, under $40, put it on checking"}'
```

The concierge, the bank, the two ride companies and the car do the rest.

<!-- @demo wait 40 -->

alice walks the ride back and checks every step.

```bash
./lab.sh alice python custody.py
```

<!-- @demo end -->
