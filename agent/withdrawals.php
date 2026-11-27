<?php
require_once __DIR__ . '/rag_client.php';

// SQLite retains the request IDs so an interrupted MySQL cleanup can be replayed.
function clearWithdrawnAnswers(PDO $pdo): array {
    $rows = rag('/admin/removed-answers', null, true);
    $removed = [];
    if (!$rows) return $removed;

    $pdo->beginTransaction();
    try {
        $update = $pdo->prepare("UPDATE queries SET answer='Источник ответа удалён.',
            human_answer=NULL, ticket_id=NULL, files_json=JSON_ARRAY(),
            rag_result=JSON_OBJECT('kind','withdrawn','withdrawn',true,'hits',JSON_ARRAY(),'used',JSON_ARRAY())
            WHERE user_id=? AND request_id=? AND JSON_UNQUOTE(JSON_EXTRACT(rag_result,'$.kind')) <> 'withdrawn'");
        $delete = $pdo->prepare("DELETE t FROM tickets t JOIN queries q ON q.id=t.query_id AND q.user_id=t.user_id
            WHERE q.user_id=? AND q.request_id=?");
        foreach ($rows as $row) {
            $params = [(int)substr($row['owner'], 5), $row['request']];
            $update->execute($params);
            $delete->execute($params);
            $removed[$row['owner'] . '/' . $row['request']] = true;
        }
        $pdo->commit();
    } catch (Throwable $e) {
        $pdo->rollBack();
        throw $e;
    }
    return $removed;
}
