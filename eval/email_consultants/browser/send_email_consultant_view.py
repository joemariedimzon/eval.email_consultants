from five import grok
from Products.CMFCore.interfaces import IContentish
from Products.CMFCore.utils import getToolByName
import json
from Products.statusmessages.interfaces import IStatusMessage
from zope.component import getAdapters
from collective.pdfexport.interfaces import IPDFEmailSource
from plone import api
from email.MIMEBase import MIMEBase
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email import Encoders
from zope.component import getMultiAdapter
import copy
import zipfile
try:
    from cStringIO import StringIO
except ImportError:
    from io import BytesIO as StringIO

import time
import os
from Products.CMFDefault.utils import checkEmailAddress
from Products.CMFDefault.exceptions import EmailAddressInvalid
from email.Header import Header

grok.templatedir('templates')

class send_email_consultant_view(grok.View):
    grok.context(IContentish)
    grok.require('zope2.View')
    grok.name('send-email-files')
    
    
    def js(self):
        path = '/'.join(self.context.getPhysicalPath())+'/consultant_cv_recipients'
        recipients = self.context.unrestrictedTraverse(path).__call__()
        
        config = {
            'theme': 'facebook',
            'tokenDelimiter': '\n',
            'preventDuplicates': True,
            'prePopulate': json.loads(recipients),
        }
        
        return '''
            $(document).ready(function () {
                $('#email-recipients').tokenInput('%s', %s);
            })
        ''' % (
            self.context.absolute_url() + '/consultant_cv_recipients',
            json.dumps(config)
        )
    
    def consultants(self):
        if self.request.form:
            if 'id' in self.request.form:
                catalog = getToolByName(self.context, 'portal_catalog')
                brains = catalog.unrestrictedSearchResults(portal_type='ploneun.consultant.consultant', UID=self.request.form['id'])
                return brains
        return None
    
    def submitted_consultants(self):
        if self.request.form:
            if 'consultants_name' in self.request.form:
                return self.request.form['consultants_name']
        return []
    
    def submitted_recipients(self):
        if self.request.method == 'POST':
            config = {}
            if self.request.get('email-recipients'):
                config['prePopulate'] = [{'id': i, 'name': i} for i in self.request.get('email-recipients').split(',')]
                return json.dumps(config)
        return
    
    def form_submitted(self):
        if self.request.method == 'POST':
            statusmessages = IStatusMessage(self.request)
            if self.request.form:
                form = self.request.form
                mark = 0
                recipients = None
                subject = ''
                message = ''
                consultants = []
                
                cc_recipients = []
                
                if 'email-recipients' in form:
                    if not form['email-recipients']:
                        statusmessages.add('Recipients is required', type='error')
                        mark += 1
                    recipients = form['email-recipients']
                if 'email-subject' in form:
                    if not form['email-subject']:
                        statusmessages.add('Subject is required', type='error')
                        mark += 1
                    subject = form['email-subject']
                if 'email-msg' in form:
                    if not form['email-msg']:
                        statusmessages.add('Message is required', type='error')
                        mark += 1
                    message = form['email-msg']
                if 'consultants_name' in form:
                    if type(form['consultants_name']) == str:
                        consultants.append(form['consultants_name'])
                    elif type(form['consultants_name']) == list:
                        consultants = copy.copy(form['consultants_name'])
                if 'email-cc' in form:
                    cc_raw = form['email-cc'].replace('\r', ',').replace('\n', '').split(',')
                    
                    cc_raw2 = []
                    if cc_raw:
                        for cc in cc_raw:
                            if validateaddress(cc.strip()):
                                cc_recipients.append(cc.strip())
                
                if mark > 0:
                    return
                else:
                    expanded_recipients = []
                    adapters = getAdapters((self.context,), IPDFEmailSource)
                    if recipients:
                        
                        for recipient in recipients.split(','):
                            expanded = False
                            recipient = recipient.strip()
                            for name, adapter in adapters:
                                
                                if adapter.can_expand(recipient):
                                    expanded_recipients += adapter.expand_value(recipient)
                                    expanded = True
                                    break
                            if not expanded:
                                expanded_recipients.append(recipient)
                            
                        self.send_email(
                            recipients=list(set(expanded_recipients)),
                            subject=subject,
                            message=message,
                            consultants=consultants,
                            cc=cc_recipients
                        )
                                
                                
    def send_email(self, recipients, subject, message, consultants, cc):
        portal_url = getToolByName(self.context, 'portal_url')
        portal_catalog = getToolByName(self.context, 'portal_catalog')
        portal_state = getMultiAdapter((self.context, self.request), name=u"plone_portal_state")
        portal = portal_url.getPortalObject()
        mFrom = portal.getProperty('email_from_address')
        statusmessages = IStatusMessage(self.request)        
        mailhost = self.context.MailHost
        from_address = api.user.get_current().getProperty('email')
        msg = MIMEMultipart()
        msg['Subject'] = subject
        
        msg['From'] = '%s <%s>' % (portal_state.portal_title(), mFrom)
        
        htmlPart = MIMEText(message, 'plain', 'utf-8')
        msg.attach(htmlPart)
        zipped = []
        timestr = time.strftime("%Y%m%d-%H%M%S")
        if consultants:
            brains = portal_catalog.unrestrictedSearchResults(portal_type='ploneun.consultant.consultant', UID=consultants)
            uploaded_files = []
            for brain in brains:
                brains2 = portal_catalog.unrestrictedSearchResults(path={'query':brain.getPath(), 'depth':1}, portal_type='File')
                for brain2 in brains2:
                    obj2 = brain2._unrestrictedGetObject()
                    
                    if obj2.getFile():
                        uploaded_files.append({'file': obj2.getFile().getBlob().open().name, 'filename':obj2.getFile().filename})
            
            if uploaded_files:
                
                zf = zipfile.ZipFile("%s.zip" % timestr, "w", zipfile.ZIP_DEFLATED)
                for uf in uploaded_files:
                    zf.write(uf['file'], uf['filename'])
                zf.close()
                attachment = MIMEBase('application', 'zip')
                attachment.set_payload(open(zf.filename).read())
                open(zf.filename).close()
                Encoders.encode_base64(attachment)
                attachment.add_header('Content-Disposition', 'attachment',
                               filename='attached_files' + '.zip')
                msg.attach(attachment)
                
                
        msg['To'] = ','.join(recipients)
        msg['cc'] = ','.join(cc)
        
        mailhost.send(msg.as_string())
        
        
        #for recipient in recipients:
        #    # skip broken recipients
        #    if not recipient:
        #        continue
        #    if '@' not in recipient:
        #        continue

        #    del msg['To']
        #    msg['To'] = recipient
        #    mailhost.send(msg.as_string())
        
        
        for f in os.listdir('.'):
            if timestr+'.zip' == f:
                os.remove(f)
        
        statusmessages.add('Emails sent')
        self.request.response.redirect(self.context.absolute_url())
        

def validateaddress(value):
    try:
        checkEmailAddress(value)
    except EmailAddressInvalid:
        return False
    return True
        
            
            


    
        