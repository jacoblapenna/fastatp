# Geese: signing up and logging in with no secret on the wire

*A skeleton: the cast, the program, and one command, so the lab and the page
can be played end to end. The tutorial itself is written on top of this.*

<!-- @demo machines geese -->
<!-- @demo peer geese geese.py -->
<!-- @demo app geese /app/ -->

<!-- @demo start -->
<!-- @demo chapter 1 "Sign up" -->
<!-- @demo blurb Geese is an app you sign up to with nothing but your browser. -->

## Sign up

Geese is one machine: the app's host.

<!-- @demo point app -->

```bash
./lab.sh geese curl -s localhost/whoami
```

A second device is the same app, with storage of its own.

<!-- @demo open phone -->

<!-- @demo end -->
