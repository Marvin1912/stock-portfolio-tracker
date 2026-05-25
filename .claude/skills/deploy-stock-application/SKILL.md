---
name: deploy-stock-application
description: Deploy the stock application. The application should be built and deployed within the Kubernetes cluster. React to the task to deploy the application.
---

1. Build and push the image: run `./build.sh` in the repository root. This builds the Docker image and pushes it to the registry at `192.168.178.29:5000/stock-portfolio-tracker:latest`.
2. Roll out the new image: run `kubectl rollout restart deployment stock-portfolio-tracker`, then wait for it with `kubectl rollout status deployment stock-portfolio-tracker`.
3. Verify: check the pod logs with `kubectl logs deployment/stock-portfolio-tracker --tail=100` for errors and confirm the application started successfully.
