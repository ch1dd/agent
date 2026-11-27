<?php
function answer_files(array $result): array {
    $files = [];
    foreach ($result['used'] ?? [] as $index) {
        $hit = $result['hits'][$index] ?? [];
        $version = $hit['version'] ?? '';
        if (is_string($version) && preg_match('/^[a-f0-9]{64}$/D', $version) && !isset($files[$version])) {
            $files[$version] = [
                'version' => $version,
                'name' => basename($hit['passage']['source']),
                'location' => $hit['passage']['location'],
                'rank' => $index + 1,
            ];
        }
    }
    return array_values($files);
}
