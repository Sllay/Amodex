UPDATE amodex_model_routes
SET enabled = FALSE,
    updated_at = NOW(),
    provenance = provenance || ' | retired 2026-11-03: use NVIDIA NIM free endpoint route instead of direct Mistral API'
WHERE route_id = 'mistral:mistral-large-4:mistral-free-primary';
