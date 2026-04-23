## Remote Hosted Ops (Railway)

After deploying a hosted service, you can run post-deploy operations from the CLI:

```bash
# inspect service status, URL, deployment metadata
opensre remote ops --provider railway --project <project> --service <service> status

# tail recent logs
opensre remote ops --provider railway --project <project> --service <service> logs --lines 200

# stream logs live
opensre remote ops --provider railway --project <project> --service <service> logs --follow

# trigger restart/redeploy
opensre remote ops --provider railway --project <project> --service <service> restart --yes
```

OpenSRE saves your last used `provider`/`project`/`service`, so you can run:

```bash
opensre remote ops status
opensre remote ops logs --follow
```

---
