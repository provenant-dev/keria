from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
import time

# Define metrics
REQUESTS = Counter('requests_total', 'Total Request Count', ['method', 'path', 'resource'])
ERRORS = Counter('errors_total', 'Total Error Count', ['method', 'path', 'resource', 'status'])
DURATION = Histogram('request_duration_seconds', 'Duration of requests', ['method', 'path', 'resource'])

class MetricsMiddleware:
    def __init__(self):
        """Initialize the middleware with Prometheus metrics."""
        self.registry = CollectorRegistry()
        self.registry.register(REQUESTS)
        self.registry.register(ERRORS)
        self.registry.register(DURATION)
    def process_request(self, req, resp):
        """Process the request before routing it."""
        req.context['resource'] = req.get_header('signify-resource')
        req.context['start_time'] = time.time()

    def process_resource(self, req, resp, resource, params):
        """Process the request after routing."""
        REQUESTS.labels(method=req.method, path=req.path, resource=req.context['resource']).inc()

    def process_response(self, req, resp, resource, req_succeeded):
        """Post-processing of the response (after routing)."""
        if 'start_time' in req.context:
            duration = time.time() - req.context['start_time']
            DURATION.labels(method=req.method, path=req.path, resource=req.context.get('resource', '')).observe(duration)

        if not req_succeeded:
            ERRORS.labels(method=req.method, path=req.path, resource=req.context.get('resource', ''), status=resp.status).inc()

class MetricsEndpoint:
    def __init__(self, registry):
        self.registry = registry
    
    def on_get(self, req, resp):
        resp.content_type = 'text/plain; version=0.0.4; charset=utf-8'
        data = generate_latest(self.registry)
        resp.body = str(data.decode('utf-8'))
