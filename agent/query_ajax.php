<?php
require __DIR__ . '/auth.php';
require_once __DIR__ . '/ask_site.php';
header('Content-Type: application/json; charset=utf-8');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    exit;
}
verify_csrf();
$userId = $_SESSION['user_id'];
session_write_close();

try {
    $query = ask_site_question($pdo, $userId, trim($_POST['question'] ?? ''), $_POST['request_id'] ?? '');
    echo json_encode(['success' => true, 'query' => $query], JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE);
} catch (Exception $e) {
    echo json_encode(['success' => false, 'error' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
}
