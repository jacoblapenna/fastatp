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

Tick keep me signed in, then press Sign up.
<!-- @demo you app POST /signup -->
<!-- @demo do app tick #keep -->
<!-- @demo do app click #signup -->

Reload this browser, then press Log in.
<!-- @demo you app GET /xrpc/com.atproto.sync.getRepo -->
<!-- @demo do app reload -->
<!-- @demo do app click #login -->

A second device is the same app, with storage of its own.

<!-- @demo open phone -->

On the second device, choose I already have an account, paste your account,
make this device's key, and press Log in.
<!-- @demo you phone GET /xrpc/com.atproto.sync.getRepo 404 -->
<!-- @demo do phone click #existing -->
<!-- @demo do phone copy #account app #did -->
<!-- @demo do phone click #mint -->
<!-- @demo do phone click #login2 -->

In this browser, paste the second device's key and press Let this device in.
<!-- @demo you app POST /push-car -->
<!-- @demo do app copy #device phone #mypen -->
<!-- @demo do app click #add -->

On the second device, press Log in again.
<!-- @demo you phone GET /xrpc/com.atproto.sync.getRepo 200 -->
<!-- @demo do phone click #login2 -->

In this browser, remove the second device.
<!-- @demo you app POST /push-car -->
<!-- @demo do app click #devices button -->

On the second device, press Log in once more.
<!-- @demo you phone GET /xrpc/com.atproto.sync.getRepo 404 -->
<!-- @demo do phone click #login2 -->

<!-- @demo end -->
