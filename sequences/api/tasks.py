import os
import math
import time
import json
import yaml
import arrow
import numpy
import base64
import pandas
import fuzzyset
import pendulum
import operator
import itertools
import subprocess
import collections
from Bio import SeqIO
from time import sleep
from O365 import Account
from functools import reduce
from dotenv import load_dotenv
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.db.models import F, Q, Count
from zipfile import ZipFile, ZIP_DEFLATED
from django.core.paginator import Paginator
# from django.db.models.functions import Cast
# from django.db.models.fields import DateField
from channels.db import database_sync_to_async
from sequences.models import Download_Handler, Metadata_Handler, Frontend_Handler, Metadata

load_dotenv(os.path.join(settings.BASE_DIR, '.env'))

@database_sync_to_async
def queryhub_api(search):
    obj = Metadata.objects
    if(search["lineage"]):
        obj = obj.filter(Lineage__in=search['lineage'])
    if(search["state"]):
        obj = obj.filter(State__in=search['state'])
    if(search["mutation"]):
        obj = obj.filter(reduce(operator.and_, (Q(aaSubstitutions__contains=x) for x in search["mutation"])) | reduce(operator.and_, (Q(aaDeletions__contains=x) for x in search["mutation"])))
    if(search["from_date"]):
        obj =obj.filter(Collection_date__gte=search['from_date'])
    if(search["to_date"]):
        obj = obj.filter(Collection_date__lte=search['to_date'])
    lineage_count = obj.values('Lineage').annotate(lineage = F('Lineage'), count = Count('Virus_name', distinct=True)).values('lineage', 'count')
    state_count = obj.values('State').annotate(state = F('State'), count = Count('Virus_name', distinct=True)).values('state', 'count')
    return {"lineage": list(lineage_count), "state": list(state_count)}

@shared_task(bind=True)
def create_config_file(self, upload_info):
    upload_date = pendulum.now('Asia/Kolkata').format('YYYY-MM-DD_hh-mm-ss-A')
    config_data = {
        "websocket": True,
        "analysis_time": upload_date,
        "base_path": settings.MEDIA_ROOT,
        "uploaded_by": upload_info['username'],
        "sequences_uploaded": upload_info['uploaded'],
    }
    configfile_loc = os.path.join(
        settings.BASE_DIR, 'workflow', 'config', f"config_{upload_date}.yaml")
    yaml.dump(config_data, open(configfile_loc, 'w'))
    snakefile_loc = os.path.join(settings.BASE_DIR, 'workflow', 'Snakefile')
    command = f"exec snakemake --snakefile {snakefile_loc} --configfile {configfile_loc} --cores 20"
    snakemake_command = subprocess.run(command, shell=True)
    return 'Pipeline run completed'

@database_sync_to_async
def get_my_batch(user_obj):
    username = user_obj.username.split('_')[1]
    # metadata_qs = Metadata_Handler.objects.filter(Q(user = user_obj)).annotate(date_only = Cast('submission_date', DateField())).values_list('date_only', flat = True).distinct()
    metadata_qs = list(Metadata_Handler.objects.filter(
        Q(user=user_obj)).values_list('submission_date', flat=True))
    return metadata_qs


@database_sync_to_async
def get_my_batch_metadata(user_obj, submission_date):
    username = user_obj.username.split('_')[1]
    metadata_qs = Metadata_Handler.objects.get(
        user=user_obj, submission_date=submission_date)
    print(metadata_qs.submission_date, metadata_qs.user, metadata_qs.metadata)
    pass


@database_sync_to_async
def get_my_metadata(user_obj, each_page, page, search=None, download=False):
    username = user_obj.username.split('_')[1]
    if(search):
        user_metadata = search_my_metadata(user_obj, search)
    else:
        user_metadata = list(Metadata.objects.filter(Submitting_lab=username).values(
            'Virus_name', 'State', 'Gender', 'Clade', 'Lineage', 'District', 'Deletions', 'Treatment',
            'aaDeletions', 'Patient_age', 'Scorpio_call', 'Substitutions', 'Submitting_lab',
            'Patient_status', 'Collection_date', 'Last_vaccinated', 'Originating_lab',
            'Assembly_method', 'aaSubstitutions', 'Sequencing_technology'
        ))
    if(not download):
        paginator = Paginator(user_metadata, each_page)
        required_metadata = list(paginator.page(page))
        data = {
            "metadata": required_metadata,
            "total_length": paginator.num_pages
        }
    else:
        required_metadata = user_metadata
        data = {
            "metadata": required_metadata
        }
    return data


@database_sync_to_async
def get_my_metadata_name(user_obj):
    metadata_qs = Metadata_Handler.objects.filter(Q(user=user_obj))
    if(metadata_qs.count() > 0):
        temp = []
        for index, i in enumerate(metadata_qs):
            temp.append(i.metadata)
        return_dict = itertools.chain(*temp)
        user_metadata = [i['Virus name'] for i in return_dict]
    else:
        user_metadata = {'message': 'No data found'}
    data = {
        "name": user_metadata,
    }
    return data


def search_my_metadata(user_obj, search):
    username = user_obj.username.split('_')[1]
    filter_columns = [
        'State__contains',
        'Clade__contains',
        'Gender__contains',
        'Lineage__contains',
        'District__contains',
        'Deletions__contains',
        'Treatment__contains',
        'Virus_name__contains',
        'aaDeletions__contains',
        'Patient_age__contains',
        'Scorpio_call__contains',
        'Substitutions__contains',
        'Patient_status__contains',
        'Collection_date__contains',
        'Last_vaccinated__contains',
        'Originating_lab__contains',
        'Assembly_method__contains',
        'aaSubstitutions__contains',
        'Sequencing_technology__contains',
    ]
    filter_statement = reduce(
        operator.or_, [Q(**{name: search}) for name in filter_columns])
    search_metadata = list(Metadata.objects.filter(
        Submitting_lab=username).filter(filter_statement).values())
    return search_metadata


def update_landing_data(source='frontend'):
    metadata_qs = Metadata_Handler.objects.all()
    frontend_obj = Frontend_Handler.objects.last()

    if(metadata_qs.count() > 0):
        temp = {}
        pie_chart_data = []
        users_updated = []
        total_sequenced = 0
        for metadata in Metadata_Handler.objects.iterator():
            user = metadata.user.username.split('_')[1]
            metadata_size = len(metadata.metadata)
            total_sequenced += metadata_size
            if(user in users_updated):
                temp[user] = {
                    "value": temp[user]["value"] + metadata_size,
                    "name": f"{user} ({temp[user]['value'] + metadata_size})",
                }
            else:
                users_updated.append(user)
                temp[user] = {
                    "value": metadata_size,
                    "name": f"{user} ({metadata_size})",
                }
        pie_chart_data = list(temp.values())

        if(source == 'frontend'):
            frontend_obj = Frontend_Handler(
                map_data=frontend_obj.map_data,
                lineage_data=frontend_obj.lineage_data,
                states_covered=frontend_obj.states_covered,
                pie_chart_data=pie_chart_data,
                scorpio_version=frontend_obj.scorpio_version,
                pangolin_version=frontend_obj.pangolin_version,
                nextclade_version=frontend_obj.nextclade_version,
                genomes_sequenced=total_sequenced,
                pangolearn_version=frontend_obj.pangolearn_version,
                variants_catalogued=frontend_obj.variants_catalogued,
                lineages_catalogued=frontend_obj.lineages_catalogued,
                constellation_version=frontend_obj.constellation_version,
                pango_designation_version=frontend_obj.pango_designation_version,

            )
            frontend_obj.save()
        elif(source == 'backend'):
            return pie_chart_data, total_sequenced


def get_dashboard():
    frontend_obj = Frontend_Handler.objects.last()
    if(frontend_obj):
        dashboard = {
            "map_data": frontend_obj.map_data,
            "lineage_data": frontend_obj.lineage_data,
            "last_updated": frontend_obj.last_updated,
            "pie_chart_data": frontend_obj.pie_chart_data,
            "states_covered": int(frontend_obj.states_covered),
            "scorpio_version": frontend_obj.scorpio_version,
            "pangolin_version": frontend_obj.pangolin_version,
            "nextclade_version": frontend_obj.nextclade_version,
            "genomes_sequenced": int(frontend_obj.genomes_sequenced),
            "pangolearn_version": frontend_obj.pangolearn_version,
            "variants_catalogued": int(frontend_obj.variants_catalogued),
            "lineages_catalogued": int(frontend_obj.lineages_catalogued),
            "constellation_version": frontend_obj.constellation_version,
            "pango_designation_version": frontend_obj.pango_designation_version,
        }
    else:
        dashboard = {
            "last_updated": 0,
            "pie_chart_data": [],
            "states_covered": 0,
            "genomes_sequenced": 0,
            "variants_catalogued": 0,
            "lineages_catalogued": 0,
        }
    return dashboard


@database_sync_to_async
def create_download_link(workflow_info):
    download_link = f"{os.getenv('DOWNLOAD_URL')}/INSACOG_data_{workflow_info['upload_time']}.zip"
    download_obj = Download_Handler(download_link=download_link)
    download_obj.save()


@database_sync_to_async
def create_frontend_entry(workflow_info):
    pie_chart_data, genomes_sequenced = update_landing_data('backend')
    download_obj = Frontend_Handler(
        pie_chart_data=pie_chart_data,
        map_data=workflow_info['map_data'],
        genomes_sequenced=genomes_sequenced,
        states_covered=workflow_info['states_covered'],
        scorpio_version=workflow_info['scorpio_version'],
        pangolin_version=workflow_info['pangolin_version'],
        nextclade_version=workflow_info['nextclade_version'],
        pangolearn_version=workflow_info['pangolearn_version'],
        variants_catalogued=workflow_info['variants_catalogued'],
        lineages_catalogued=workflow_info['lineages_catalogued'],
        lineage_data=workflow_info['lineage_graph_data']['lineage'],
        constellation_version=workflow_info['constellation_version'],
        pango_designation_version=workflow_info['pango_designation_version'],
    )
    download_obj.save()
    create_metadata_entry.delay(workflow_info['metadata_link'])


@shared_task(bind=True)
def create_metadata_entry(self, metadata_link):
    entries = []
    metadata = pandas.read_csv(
        metadata_link, delimiter='\t', encoding='utf-8', low_memory=False)
    if(Metadata.objects.count() > 0):
        print('Truncation starting')
        Metadata.truncate()
        print('Truncation done')
    for i in metadata.index:
        entries.append(
            Metadata(
                State=metadata['State'][i],
                Clade=metadata['clade'][i],
                Gender=metadata['Gender'][i],
                Lineage=metadata['lineage'][i],
                District=metadata['District'][i],
                Deletions=metadata['deletions'][i],
                Treatment=metadata['Treatment'][i],
                Virus_name=metadata['Virus name'][i],
                aaDeletions=metadata['aaDeletions'][i],
                Patient_age=metadata['Patient age'][i],
                Scorpio_call=metadata['scorpio_call'][i],
                Substitutions=metadata['substitutions'][i],
                Submitting_lab=metadata['Submitting lab'][i],
                Patient_status=metadata['Patient status'][i],
                Collection_date=metadata['Collection date'][i],
                Last_vaccinated=metadata['Last vaccinated'][i],
                Originating_lab=metadata['Originating lab'][i],
                Assembly_method=metadata['Assembly method'][i],
                aaSubstitutions=metadata['aaSubstitutions'][i],
                Sequencing_technology=metadata['Sequencing technology'][i],
            )
        )
    print('Entries created')
    batch_size = 500
    bulk_obj = Metadata.objects.bulk_create(entries, batch_size)
    print('Bulk creation done')


def send_email_upload(user_info):
    credentials = (os.getenv('ONEDRIVE_CLIENT'), os.getenv('ONEDRIVE_SECRET'))
    account = Account(credentials, auth_flow_type='authorization')
    if(account.is_authenticated):
        message = account.new_message()
        if(eval(os.getenv('DEBUG'))):
            message.to.add(['aks1@nibmg.ac.in'])
            message.subject = '🗣️|📤 Upload Info [ INSACOG TestHub ]'
        else:
            message.to.add(['aks1@nibmg.ac.in', 'nkb1@nibmg.ac.in', 'ap3@nibmg.ac.in',
                           'data.analyst.insacog@nibmg.ac.in', 'manager.insacog@nibmg.ac.in'])
            message.subject = '✅|📤 Upload Info [ INSACOG DataHub ]'
        html_content = f"""
            <div>
                Dear all,
                    <p>
                        This is an automated mail to alert you of the submission of
                        <strong style="background-color:#FFC748;text-decoration:none;">{ user_info['uploaded'] } samples by
                        { user_info['username'].split('_')[1] }</strong>.
                    </p>
                    <p>
                    With Regards,<br>
                    INSACOG DataHub
                    </p>
            </div>
        """
        message.body = html_content
        try:
            response = message.send()
            if(response):
                return "Mail sent"
            return "Mail couldn\'t be sent"
        except Exception as e:
            print(e.message)
            return "Mail couldn\'t be sent"
    else:
        print('Authentication not done')


def send_email_success(workflow_info):
    start_time = pendulum.from_format(workflow_info["upload_time"], "YYYY-MM-DD_hh-mm-ss-A", tz="Asia/Kolkata")
    end_time = pendulum.from_format(workflow_info["timestamp"], "YYYY-MM-DD_hh-mm-ss-A", tz="Asia/Kolkata")
    credentials = (os.getenv('ONEDRIVE_CLIENT'), os.getenv('ONEDRIVE_SECRET'))
    account = Account(credentials, auth_flow_type='authorization')
    if(account.is_authenticated):
        storage = account.storage()
        my_drive = storage.get_default_drive()
        search_files = my_drive.search(workflow_info["upload_time"], limit=1)
        link = ""
        for i in search_files:
            link = i.share_with_link(share_type='view').share_link
        message1 = account.new_message()
        message2 = account.new_message()
        message1.to.add(['aks1@nibmg.ac.in'])
        if(eval(os.getenv('DEBUG'))):
            message2.to.add(['animesh.workplace@gmail.com'])
            message1.subject = '🗣️|📦 Report v2 [ INSACOG TestHub ]'
            message2.subject = '🗣️|📦 Report v2 [ INSACOG TestHub ]'
        else:
            message2.bcc.add(['samastha849@gmail.com'])
            message2.to.add(['nkb1@nibmg.ac.in', 'ap3@nibmg.ac.in',
                            'data.analyst.insacog@nibmg.ac.in', 'manager.insacog@nibmg.ac.in'])
            message1.subject = '📦 Report v2 [ INSACOG DataHub ]'
            message2.subject = '📦 Report v2 [ INSACOG DataHub ]'
        html_content1 = f"""
            <div>
                Dear all,
                    <p>
                        This is an automated mail to provide the link for the report generated after the daily analysis of
                        <strong style="background-color:#FFC748;text-decoration:none;">{ workflow_info['total_seq'] }
                        sequences in the backend and in the frontend it's showing { workflow_info['frontend_seq'] }</strong>.<br>
                        The pipeline has analyzed total
                        <strong style="background-color:#FFC748;text-decoration:none;">{ workflow_info['total_seq'] } sequences</strong> and took
                        <strong style="background-color:#FFC748;text-decoration:none;">{ end_time.diff(start_time).in_minutes() } minutes</strong> starting at
                        <strong style="background-color:#FFC748;text-decoration:none;">{ start_time.to_day_datetime_string() }</strong> and completing at
                        <strong style="background-color:#FFC748;text-decoration:none;">{ end_time.to_day_datetime_string() }</strong> to generate reports.
                    </p>
                    <p>
                        The link contains following files:
                        <ul>
                            <li>Raw metadata and sequences [ Combined ]</li>
                            <li>Aligned sequences using mafft</li>
                            <li>Nextstrain formatted metadata and sequences [ Ready to use ]</li>
                            <li>Combined metadata with all information in INSACOG DataHub metadata format</li>
                            <li>Clade and Lineage report</li>
                            <li>Mutation count report for all and State wise</li>
                            <li>Specific VoC/VoI ID report for all and State wise</li>
                            <li>Specific VoC/VoI progression report for all and State wise</li>
                            <li>Lineage deletion/substitution report</li>
                            <li>Logs</li>
                        </ul>
                    </p>
                    <p>
                        <a href="{ link }" target="_blank"
                            style="background-color:#1b1d1e;border:1px solid #373b3e;border-radius:8px;color:#c6c1b9;display:inline-block;font-size:14px;font-weight:bold;line-height:36px;text-align:center;text-decoration:none;width:200px;"
                        >
                            Click here to go to Drive
                        </a>
                    </p>
                    <p>
                        With Regards,<br>
                        INSACOG DataHub
                    </p>
            </div>
        """
        html_content2 = f"""
            <div>
                Dear all,
                    <p>
                        This is an automated mail to provide the link for the report generated after the daily analysis of
                        <strong style="background-color:#FFC748;text-decoration:none;">{ workflow_info['total_seq'] }
                        sequences</strong>.<br>
                    </p>
                    <p>
                        The link contains following files:
                        <ul>
                            <li>Raw metadata and sequences [ Combined ]</li>
                            <li>Aligned sequences using mafft</li>
                            <li>Nextstrain formatted metadata and sequences [ Ready to use ]</li>
                            <li>Combined metadata with all information in INSACOG DataHub metadata format</li>
                            <li>Clade and Lineage report</li>
                            <li>Mutation count report for all and State wise</li>
                            <li>Specific VoC/VoI ID report for all and State wise</li>
                            <li>Specific VoC/VoI progression report for all and State wise</li>
                            <li>Lineage deletion/substitution report</li>
                            <li>Logs</li>
                        </ul>
                    </p>
                    <p>
                        <a href="{ link }" target="_blank"
                            style="background-color:#1b1d1e;border:1px solid #373b3e;border-radius:8px;color:#c6c1b9;display:inline-block;font-size:14px;font-weight:bold;line-height:36px;text-align:center;text-decoration:none;width:200px;"
                        >
                            Click here to go to Drive
                        </a>
                    </p>
                    <p>
                        With Regards,<br>
                        INSACOG DataHub
                    </p>
            </div>
        """
        message1.body = html_content1
        message2.body = html_content2
        try:
            response1 = message1.send()
            response2 = message2.send()
            if(response1 and response2):
                return "Mail sent"
            return "Mail couldn\'t be sent"
        except Exception as e:
            print(e.message)
            return "Mail couldn\'t be sent"
    else:
        print('Authentication not done')


def send_email_error(workflow_info):
    start_time = pendulum.from_format(
        workflow_info["upload_time"], "YYYY-MM-DD_hh-mm-ss-A", tz="Asia/Kolkata")
    end_time = pendulum.from_format(
        workflow_info["timestamp"], "YYYY-MM-DD_hh-mm-ss-A", tz="Asia/Kolkata")
    credentials = (os.getenv('ONEDRIVE_CLIENT'), os.getenv('ONEDRIVE_SECRET'))
    account = Account(credentials, auth_flow_type='authorization')
    if(account.is_authenticated):
        message = account.new_message()
        message.to.add(['aks1@nibmg.ac.in'])
        if(eval(os.getenv('DEBUG'))):
            message.subject = f"☠️🆘☠️ Error Information [ INSACOG TestHub ]"
        else:
            message.subject = f"☠️🆘☠️ Error Information [ INSACOG DataHub ]"
        html_content = f"""
            <div>
                Dear Animesh Kumar Singh,
                    <p>
                        This is an automated mail to alert you of an error that occured during the analysis
                        and report generation which was started after the submission of
                        <strong style="background-color:#FFC748;text-decoration:none;">{ workflow_info['uploaded'] } samples by
                        { workflow_info['username'].split('_')[1] }</strong>. The workflow started at
                        <strong style="background-color:#FFC748;text-decoration:none;">{ start_time.to_day_datetime_string() }</strong> and error occured at
                        <strong style="background-color:#FFC748;text-decoration:none;">{ end_time.to_day_datetime_string() }</strong>.
                    </p>

                    <p>
                        Traceback of the error which occured in the step <strong style="background-color:#FFC748;text-decoration:none;">{ workflow_info['tool'] }</strong>
                    </p>

                    <hr>
                        <pre style="font-family:'Open Sans';background-color:#E5F1DC;text-decoration:none;">{ workflow_info['message'] }</pre>
                    <hr>

                    <p>
                    With Regards,<br>
                    INSACOG DataHub
                    </p>
            </div>
        """
        message.body = html_content
        try:
            response = message.send()
            if(response):
                return "Mail sent"
            return "Mail couldn\'t be sent"
        except Exception as e:
            print(e.message)
            return "Mail couldn\'t be sent"
    else:
        print('Authentication not done')
