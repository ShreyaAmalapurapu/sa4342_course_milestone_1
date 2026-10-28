id):
    z=c.execute('SELECT last_event FROM reads WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
    last=z['last_event'] if z else 0
    unread=c.execute('SELECT count(*) n FROM events e LEFT JOIN messages m ON m.event_id=e.event_id AND m.channel_id=e.channel_id WHERE e.channel_id=? AND e.event_id>? AND (m.author_id IS NULL OR m.author_id!=?)',(cid,last,uid)).fetchone()['n']
    mention=c.execute("SELECT count(*) n FROM messages WHERE channel_id=? AND author_id!=? AND deleted=0 AND mentions LIKE ? AND event_id> ?",(cid,uid,'%'+str(uid)+'%',last)).fetchone()['n']
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}
