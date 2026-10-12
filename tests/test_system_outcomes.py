import pytest
from core.mail_outcomes import classify_email_stage
from core.jobs import JobRepository
from core import jobs

@pytest.mark.parametrize('text,stage',[
 ('Thank you for your application. We are unable to progress your application further.','Rejected'),
 ('Regarding your application: we will not be moving forward with your application.','Rejected'),
 ('Regarding your application: we have decided not to move forward with your application.','Rejected'),
 ('Regarding your application: we are unable to offer technical support today.','Application Update'),
 ('Your application has not been unsuccessful; we invite you to your interview.','Interview'),
 ('Your interview is scheduled. Previous email: your application was unsuccessful.','Interview'),
 ('If your application is unsuccessful, contact recruitment.',None),
 ('Thank you for your application.','Applied'),
 ('We invite you to your assessment centre.','Interview'),
 ('If you receive an offer letter, contact recruitment.',None),
])
def test_outcome_contract(text,stage):assert classify_email_stage(text)==stage

def test_source_and_sheet_objects_merge_without_losing_evidence(tmp_path,monkeypatch):
 monkeypatch.setattr(jobs,'JOBS_FILE',str(tmp_path/'jobs.json'))
 jobs.atomic_write_json(jobs.JOBS_FILE,{
 'source':{'id':'source','company':'Marshall Wace','title':'Technology Intern 2027','link':'https://example.org/job','stages':['Applied'],'email_events':[{'id':'mail','stage':'Applied','subject':'Application','sheet_synced':True}]},
 'sheet':{'id':'sheet','company':'Marshall Wace','title':'Technology Intern 2027','link':'','stages':['Applied'],'sheet_managed':True,'sheet_present':True,'email_events':[]}})
 result=JobRepository().sync([], [{'company':'Marshall Wace','role':'Technology Intern 2027','stages':['Applied']}],reconcile=True)
 assert len(result)==1 and result[0].sheet_present and result[0].link=='https://example.org/job'
 assert result[0].email_events[0]['id']=='mail'
