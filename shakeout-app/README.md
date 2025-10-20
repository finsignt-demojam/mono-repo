# Overview

This is a simple shakeout app to test routes and use of persistent volumes on Single Node OpenShift.

The source repository is located here: https://github.com/bryonbaker/shakeout-app

This app will simply deployt it, will record the pod name that received the request. It also writes the entry to a log file.

The value of this app is it tests the storage class as well as network connectivity.

# Instructions

Then find the route and open  the route in a browser with the path `hello`.

E.g. 
```
$ oc get routes
NAME                 HOST/PORT                                                 PATH   SERVICES               PORT   TERMINATION   WILDCARD
shakeout-app-route   shakeout-app-route-shakeout.apps.kmarthub.bakerapps.net          shakeout-app-service   9000                 None
```

Then navigate to `http://shakeout-app-route-shakeout.apps.kmarthub.bakerapps.net/hello`

