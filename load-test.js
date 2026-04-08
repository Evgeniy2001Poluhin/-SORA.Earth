import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 5 },
    { duration: '1m', target: 10 },
    { duration: '1m', target: 20 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1000'],
    checks: ['rate>0.95'],
  },
};

const BASE = __ENV.BASE_URL || 'http://host.docker.internal:8000';

const predictPayload = JSON.stringify({
  name: 'Load RF',
  budget: 75000,
  co2_reduction: 60,
  social_impact: 7,
  duration_months: 18,
  category: 'Solar Energy',
  region: 'Germany'
});

const evaluatePayload = JSON.stringify({
  name: 'Load Eval',
  budget: 120000,
  co2_reduction: 72,
  social_impact: 8,
  duration_months: 24,
  category: 'Solar Energy',
  region: 'Germany'
});

const params = {
  headers: { 'Content-Type': 'application/json' },
};

export default function () {
  const r1 = http.post(`${BASE}/predict`, predictPayload, params);
  check(r1, {
    'predict status 200': (r) => r.status === 200,
    'predict has probability': (r) => {
      try {
        return JSON.parse(r.body).probability !== undefined;
      } catch (_) {
        return false;
      }
    },
  });

  const r2 = http.post(`${BASE}/evaluate`, evaluatePayload, params);
  check(r2, {
    'evaluate status 200': (r) => r.status === 200,
    'evaluate has total_score': (r) => {
      try {
        return JSON.parse(r.body).total_score !== undefined;
      } catch (_) {
        return false;
      }
    },
  });

  sleep(1);
}
