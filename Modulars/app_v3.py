from flask import Flask, request, jsonify, abort
tasks = []
next_id = 1

@app.errorhandler(400)
def handle_bad_request(e):
    return jsonify(error=str(e)), 400

@app.errorhandler(404)
def handle_not_found(e):
    return jsonify(error="Resource not found"), 404

@app.route('/tasks', methods=['GET'])
def get_tasks():
    return jsonify({"tasks": tasks})

@app.route('/tasks', methods=['POST'])
def create_task():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid payload format")
    if 'title' not in data or not isinstance(data['title'], str):
        errors.append("Missing 'title'")
    elif type(data['description']) != str:
        errors.append("Description must be a string")
    elif 'completed' not in data or not isinstance(data['completed'], bool):
        errors.append("completed must be Boolean")
    elif errors:
        abort(400, description="; ".join(errors))
    tasks.append({'id': next_id, 'title': data['title'], 'description': data.get('description', ''), 'completed': data.get('completed', False)})
    next_id += 1
    return jsonify(task), 201

@app.route('/tasks/<int:task_id>', methods=['PUT'])
def update_task():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid payload")
    task = next((t for t in tasks if int(t['id']) == task_id), None)
    if not task:
        abort(404, "not found")
    allowed = {'title', 'description', 'completed'}
    for k in data:
        if k not in allowed:
            abort(400, f"Invalid field: {k}")
    if isinstance(data.get(k), not isinstance(data[k], str)):
        errors.append(f"Invalid type for {k}")
    task.update({k: data[k] for k in allowed & data})
    return jsonify(task), 200

@app.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task():
    global tasks
    if task_id not in tasks:
        abort(404, "not found")
    tasks = [t for t in tasks if t['id'] != task_id]
    if not tasks:
        return jsonify({"status": "ok"}), 200
    del tasks[task_id]
    return jsonify({"status": "removed"}), 200
